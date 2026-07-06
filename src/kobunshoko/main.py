"""FastAPIアプリケーション・ルーティング。

サーバーは 127.0.0.1 のみにバインドする（外部公開オプションは設けない）。
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import re
import shutil
import tempfile
import unicodedata
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, ingest, render, verify, watch
from .config import Config, load_config

logger = logging.getLogger(__name__)

PKG_DIR = Path(__file__).parent
TNO_RE = re.compile(r"^[0-9A-Za-z_-]+$")

# iframe内に埋め込む変換HTML用のCSP。sandbox属性はスクリプトを遮断するが
# 受動的な外部リソース（img・CSSのurl()・meta refresh等）の読み込みは止めないため、
# CSPで外部への通信を一切禁止する（外部送信ゼロ・NFR-03）。
# 画像は同一オリジン（/doc/... の原本配信）とdata:のみ、スタイルはインラインのみ許可。
IFRAME_CSP_META = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'\">"
)

# 旧式マークアップ（IE時代の属性）向けの軽いCSS正規化。srcdoc冒頭に注入する。
IFRAME_BASE_CSS = (
    "<style>"
    "body{margin:16px;color:#1a1a1a;background:#fff;"
    "font-family:'Hiragino Sans','Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif;"
    "font-size:14px;line-height:1.8;}"
    "table{border-collapse:separate;}"
    "td,th{vertical-align:top;}"
    # IEは white-space:pre のままでも word-wrap で折り返していた（e-Gov様式は
    # IE前提でこの挙動に依存している）。現代ブラウザで同じ折り返しを再現する
    "pre{white-space:pre-wrap;overflow-wrap:break-word;}"
    "img{max-width:100%;}"
    "a{color:#0b57d0;}"
    ".xmltree dl{margin:0 0 0 1.2em;}"
    ".xmltree dt{font-weight:600;color:#444;margin-top:.3em;}"
    ".xmltree dd{margin:0 0 0 1.2em;}"
    ".xmltree .attr{font-weight:400;color:#888;font-size:12px;}"
    ".xmltree .empty{color:#aaa;}"
    "</style>"
)


def build_srcdoc(body_html: str, base_href: str | None = None) -> str:
    """iframe(sandbox) の srcdoc に入れるHTMLを組み立てる。

    base を注入すると、XSL出力内の相対リンク（DOCLINK等）が
    /doc/{tno}/view/ 配下の閲覧URLに解決される。
    """
    prefix = ""
    if base_href:
        prefix = f'<base href="{base_href}" target="_blank">'
    return IFRAME_CSP_META + prefix + IFRAME_BASE_CSS + body_html


def resolve_file(config: Config, conn, tno: str, name: str):
    """パス検証（設計 §9-1）。

    tno は英数字等のみ、name は書庫の文書セット配下に解決されること、
    かつNFC正規化後にDBの files.name に登録済みであることを要求する。
    """
    if not TNO_RE.fullmatch(tno):
        raise HTTPException(status_code=404, detail="not found")
    base = (config.docs_dir / tno).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=404, detail="not found")
    nfc_name = unicodedata.normalize("NFC", name)
    row = db.get_file(conn, tno, nfc_name)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    path = base / nfc_name
    if not path.is_file():
        # macOSのファイルシステムはNFDで保持していることがある
        alt = base / unicodedata.normalize("NFD", nfc_name)
        if alt.is_file():
            path = alt
        else:
            raise HTTPException(status_code=404, detail="not found")
    return row, path


def _rfc5987_disposition(disposition: str, filename: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download"
    return (
        f"{disposition}; filename=\"{fallback}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    config.ensure_dirs()
    with closing(db.connect(config.db_path)) as conn:
        db.ensure_schema(conn)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 監視フォルダ（FR-08）: 設定時のみバックグラウンドスレッドで常駐させる
        watcher = None
        if config.watch_dir is not None:
            watcher = watch.WatchService(config)
            watcher.start()
            app.state.watcher = watcher
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()

    app = FastAPI(title="公文書庫", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.config = config
    templates = Jinja2Templates(directory=str(PKG_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")

    def open_conn():
        conn = db.connect(config.db_path)
        # サーバー稼働中に catalog.db が削除されても500にしない（受け入れ基準7）:
        # スキーマ欠損を検知したらその場で作り直し、一覧画面（再インデックス
        # ボタンのある導線）へ到達できるようにする
        if not db.has_schema(conn):
            db.ensure_schema(conn)
        return closing(conn)

    # --- 一覧（FR-03）・全文検索（FR-05） ------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        agency: str | None = None,
        month: str | None = None,
        sort: str = "received",
        q: str | None = None,
        tag: str | None = None,
    ):
        if sort not in ("received", "ingested"):
            sort = "received"
        q = (q or "").strip()
        with open_conn() as conn:
            documents = db.list_documents(
                conn, agency=agency, month=month, sort=sort, q=q, tag=tag
            )
            agencies = db.list_agencies(conn)
            months = db.list_months(conn)
            tags = db.all_tags(conn)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "documents": documents,
                "agencies": agencies,
                "months": months,
                "tags": tags,
                "agency": agency or "",
                "month": month or "",
                "sort": sort,
                "q": q,
                "tag": tag or "",
            },
        )

    # --- 取り込み（FR-01） ---------------------------------------------------

    @app.post("/ingest", response_class=HTMLResponse)
    def ingest_upload(request: Request, files: list[UploadFile]):
        results: list[ingest.IngestResult] = []
        with open_conn() as conn:
            for upload in files:
                filename = Path(upload.filename or "upload.zip").name
                tmpdir = Path(tempfile.mkdtemp(prefix="kobunshoko-upload-"))
                try:
                    tmp_zip = tmpdir / filename
                    with tmp_zip.open("wb") as out:
                        shutil.copyfileobj(upload.file, out)
                    results.append(ingest.ingest_zip(config, conn, tmp_zip))
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
        return templates.TemplateResponse(
            request, "ingest_result.html", {"results": results}
        )

    @app.post("/ingest/path", response_class=HTMLResponse)
    def ingest_path(request: Request, path: str = Form(...)):
        target = Path(path.strip()).expanduser()
        with open_conn() as conn:
            if target.is_dir():
                results = [ingest.ingest_folder(config, conn, target)]
            elif target.is_file() and target.suffix.lower() == ".zip":
                results = [ingest.ingest_zip(config, conn, target)]
            else:
                results = [
                    ingest.IngestResult(
                        source=str(target),
                        status="error",
                        message="パスが存在しないか、ZIPファイル／フォルダではありません",
                    )
                ]
        return templates.TemplateResponse(
            request, "ingest_result.html", {"results": results}
        )

    # --- 過去分の一括取り込み（FR-07） ---------------------------------------

    @app.get("/scan", response_class=HTMLResponse)
    def scan(request: Request, dir: str = ""):
        raw = dir.strip()
        target = Path(raw).expanduser() if raw else None
        candidates = []
        error = None
        if target is None or not target.is_dir():
            error = "ディレクトリが存在しません" if raw else None
        else:
            with open_conn() as conn:
                candidates = ingest.scan_directory(config, conn, target)
        return templates.TemplateResponse(
            request,
            "scan.html",
            {
                "dir": raw,
                "candidates": candidates,
                "error": error,
            },
        )

    @app.post("/ingest/bulk", response_class=HTMLResponse)
    def ingest_bulk(request: Request, paths: list[str] = Form(default=[])):
        results: list[ingest.IngestResult] = []
        with open_conn() as conn:
            for raw in paths:
                target = Path(raw.strip()).expanduser()
                if target.is_dir():
                    results.append(ingest.ingest_folder(config, conn, target))
                elif target.is_file() and target.suffix.lower() == ".zip":
                    results.append(ingest.ingest_zip(config, conn, target))
                else:
                    results.append(
                        ingest.IngestResult(
                            source=str(target),
                            status="error",
                            message="パスが存在しないか、ZIPファイル／フォルダではありません",
                        )
                    )
        return templates.TemplateResponse(
            request, "ingest_result.html", {"results": results}
        )

    # --- 再インデックス（NFR-02） --------------------------------------------

    @app.post("/reindex", response_class=HTMLResponse)
    def reindex(request: Request):
        with open_conn() as conn:
            db.ensure_schema(conn)  # catalog.db が消されていても走査から再構築する
            results = ingest.reindex(config, conn)
        return templates.TemplateResponse(
            request,
            "ingest_result.html",
            {"results": results, "heading": "再インデックス結果"},
        )

    # --- 改ざん検知（FR-06） -------------------------------------------------

    @app.post("/doc/{tno}/verify")
    def verify_doc(tno: str):
        if not TNO_RE.fullmatch(tno):
            raise HTTPException(status_code=404, detail="not found")
        with open_conn() as conn:
            if db.get_document(conn, tno) is None:
                raise HTTPException(status_code=404, detail="not found")
            verify.verify_document(config, conn, tno)
        return RedirectResponse(f"/doc/{tno}", status_code=303)

    # --- タグ・メモ（FR-09。別テーブルに保存し、原本には書き込まない） --------

    def _require_document(conn, tno: str) -> None:
        if not TNO_RE.fullmatch(tno) or db.get_document(conn, tno) is None:
            raise HTTPException(status_code=404, detail="not found")

    @app.post("/doc/{tno}/tags")
    def add_tag(tno: str, tag: str = Form(...)):
        value = tag.strip()
        with open_conn() as conn:
            _require_document(conn, tno)
            if value:
                db.add_tag(conn, tno, value)
        return RedirectResponse(f"/doc/{tno}", status_code=303)

    @app.post("/doc/{tno}/tags/delete")
    def delete_tag(tno: str, tag: str = Form(...)):
        with open_conn() as conn:
            _require_document(conn, tno)
            db.remove_tag(conn, tno, tag)
        return RedirectResponse(f"/doc/{tno}", status_code=303)

    @app.post("/doc/{tno}/memos")
    def add_memo(tno: str, body: str = Form(...)):
        value = body.strip()
        with open_conn() as conn:
            _require_document(conn, tno)
            if value:
                created_at = datetime.now().astimezone().isoformat(timespec="seconds")
                db.add_memo(conn, tno, value, created_at)
        return RedirectResponse(f"/doc/{tno}", status_code=303)

    @app.post("/doc/{tno}/memos/{memo_id}/delete")
    def delete_memo(tno: str, memo_id: int):
        with open_conn() as conn:
            _require_document(conn, tno)
            db.delete_memo(conn, tno, memo_id)
        return RedirectResponse(f"/doc/{tno}", status_code=303)

    # --- 文書セット詳細 ------------------------------------------------------

    @app.get("/doc/{tno}", response_class=HTMLResponse)
    def doc_detail(request: Request, tno: str):
        if not TNO_RE.fullmatch(tno):
            raise HTTPException(status_code=404, detail="not found")
        with open_conn() as conn:
            document = db.get_document(conn, tno)
            if document is None:
                raise HTTPException(status_code=404, detail="not found")
            file_rows = db.list_files(conn, tno)
            doc_tags = db.list_tags(conn, tno)
            memos = db.list_memos(conn, tno)

        base = (config.docs_dir / tno).resolve()
        files_ctx = []
        kagami_name = None
        for row in file_rows:
            path = base / row["name"]
            if not path.is_file():
                alt = base / unicodedata.normalize("NFD", row["name"])
                path = alt if alt.is_file() else None
            files_ctx.append(
                {
                    "name": row["name"],
                    "role": row["role"],
                    "size": path.stat().st_size if path else None,
                    "digest_ok": row["digest_ok"],
                    # 閲覧可否は拡張子で決める（既存登録の役割がotherでもリンクを出す）
                    "viewable": Path(row["name"]).suffix.lower()
                    in (".xml", ".csv", ".pdf", ".dta"),
                }
            )
            if row["role"] == "kagami" and kagami_name is None:
                kagami_name = row["name"]

        kagami_srcdoc = None
        kagami_fallback = None
        if kagami_name:
            kagami_path = base / kagami_name
            if not kagami_path.is_file():
                kagami_path = base / unicodedata.normalize("NFD", kagami_name)
            html_text, reason = render.transform_xml(kagami_path, base)
            if html_text is None:
                kagami_fallback = reason
                html_text = render.xml_tree_html(kagami_path)
            kagami_srcdoc = build_srcdoc(html_text, base_href=f"/doc/{tno}/view/")

        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "doc": dict(document),
                "files": files_ctx,
                "tags": doc_tags,
                "memos": memos,
                "kagami_srcdoc": kagami_srcdoc,
                "kagami_fallback": kagami_fallback,
            },
        )

    # --- 閲覧（FR-04） -------------------------------------------------------

    @app.get("/doc/{tno}/view/{name:path}", response_class=HTMLResponse)
    def view_file(request: Request, tno: str, name: str):
        with open_conn() as conn:
            row, path = resolve_file(config, conn, tno, name)
            document = db.get_document(conn, tno)
        base = (config.docs_dir / tno).resolve()
        file_name = row["name"]
        raw_url = f"/doc/{tno}/raw/{quote(file_name, safe='/')}"
        suffix = Path(file_name).suffix.lower()
        ctx = {
            "tno": tno,
            "doc": dict(document) if document else None,
            "file_name": file_name,
            "raw_url": raw_url,
            "fallback_reason": None,
        }
        if suffix == ".xml":
            html_text, reason = render.transform_xml(path, base)
            if html_text is None:
                ctx["fallback_reason"] = reason
                html_text = render.xml_tree_html(path)
            ctx["kind"] = "html"
            ctx["srcdoc"] = build_srcdoc(html_text, base_href=f"/doc/{tno}/view/")
        elif suffix == ".csv":
            rows = render.read_csv_rows(path)
            if rows is None:
                ctx["kind"] = "undecodable"
            else:
                ctx["kind"] = "csv"
                ctx["rows"] = rows
        elif suffix == ".pdf":
            ctx["kind"] = "pdf"
        elif suffix == ".dta":
            lines, encrypted_bytes = render.read_dta_preview(path)
            ctx["kind"] = "dta"
            ctx["dta_lines"] = lines
            ctx["dta_encrypted_bytes"] = encrypted_bytes
        else:
            return RedirectResponse(raw_url)
        return templates.TemplateResponse(request, "view.html", ctx)

    # --- 印刷用ページ（ブラウザの「PDFとして保存」向け） -----------------------

    @app.get("/doc/{tno}/print/{name:path}", response_class=HTMLResponse)
    def print_file(tno: str, name: str):
        """変換HTMLをiframeなしの単独ページで返す。

        sandbox付きiframeの中身は親から print() を呼べないため、
        PDF保存はこのページをブラウザの印刷機能で行う。スクリプト遮断は
        sandboxの代わりにCSP（default-src 'none'）で担保する。
        """
        with open_conn() as conn:
            row, path = resolve_file(config, conn, tno, name)
        base = (config.docs_dir / tno).resolve()
        file_name = row["name"]
        if Path(file_name).suffix.lower() != ".xml":
            return RedirectResponse(f"/doc/{tno}/view/{quote(file_name, safe='/')}")
        html_text, _reason = render.transform_xml(path, base)
        if html_text is None:
            html_text = render.xml_tree_html(path)
        hint = (
            "<style>"
            ".print-hint{position:sticky;top:0;background:#fef7e0;"
            "border-bottom:1px solid #f2d692;padding:8px 16px;font-size:13px;"
            "font-family:sans-serif;}"
            "@media print{.print-hint{display:none;}}"
            "</style>"
            '<div class="print-hint">ブラウザの印刷（⌘P / Ctrl+P）から'
            "「PDFとして保存」を選んでください。この案内は印刷・PDFには含まれません。"
            f'　<a href="/doc/{tno}" target="_self">← 文書セットへ戻る</a></div>'
        )
        return HTMLResponse(
            build_srcdoc(hint + html_text, base_href=f"/doc/{tno}/view/"),
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; img-src 'self' data:; "
                    "style-src 'unsafe-inline'"
                )
            },
        )

    # --- 原本配信（FR-04） ---------------------------------------------------

    @app.get("/doc/{tno}/raw/{name:path}")
    def raw_file(tno: str, name: str):
        with open_conn() as conn:
            row, path = resolve_file(config, conn, tno, name)
        data = path.read_bytes()  # 原本バイト列をそのまま返す
        basename = Path(row["name"]).name
        suffix = Path(basename).suffix.lower()
        if suffix == ".pdf":
            media_type = "application/pdf"
            disposition = "inline"
        elif suffix == ".xml":
            # text/xml で返すとブラウザがXSLTを試みて挙動が環境依存になる（D-04）
            media_type = "text/plain; charset=utf-8"
            disposition = "inline"
        else:
            media_type = (
                mimetypes.guess_type(basename)[0] or "application/octet-stream"
            )
            disposition = "attachment"
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "Content-Disposition": _rfc5987_disposition(disposition, basename)
            },
        )

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="kobunshoko", description="公文書庫サーバー")
    parser.add_argument("--archive", help="書庫ルート（既定: $KOBUNSHOKO_ARCHIVE または ~/kobunshoko-archive）")
    parser.add_argument(
        "--watch-dir",
        help="監視フォルダ。指定するとここに置かれた公文書ZIPを自動取り込みする"
        "（既定: $KOBUNSHOKO_WATCH_DIR。未指定なら無効）",
    )
    parser.add_argument("--port", type=int, default=8720)
    args = parser.parse_args()

    import uvicorn

    config = load_config(args.archive, watch_dir=args.watch_dir)
    logger.info("書庫: %s", config.archive)
    if config.watch_dir is not None:
        logger.info("監視フォルダ: %s", config.watch_dir)
    # 127.0.0.1 バインド固定（NFR-03）。外部公開オプションは設けない
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)


def __getattr__(name: str):
    # `uvicorn kobunshoko.main:app` でも起動できるように、参照時にアプリを構築する
    if name == "app":
        return create_app()
    raise AttributeError(name)
