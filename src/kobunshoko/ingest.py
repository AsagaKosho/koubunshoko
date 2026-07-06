"""取り込みパイプライン（ZIP / フォルダ）。

入力 → [1] 展開・正規化 → [2] 鑑XML特定 → [3] メタデータ抽出
     → [4] 到達番号確定 → [5] 重複チェック → [6] 書庫へ複製 → [7] カタログ登録

文書セット単位で all-or-nothing。[1]〜[4] は一時ディレクトリで行い、
失敗時は書庫・DBに一切痕跡を残さない。取り込み元ファイルは変更しない。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from . import db
from .config import Config
from .extract import (
    FALLBACK_PATTERN_NAME,
    Meta,
    fallback_meta,
    match_pattern,
    wareki_to_iso,
)

logger = logging.getLogger(__name__)

TNO_RE = re.compile(r"^[0-9A-Za-z_-]+$")
_TNO_DIGITS_RE = re.compile(r"\d{14,20}")
_DSIG_SIGNATURE = "{http://www.w3.org/2000/09/xmldsig#}Signature"

ORIGINAL_ZIP_NAME = "original.zip"


def make_xml_parser() -> etree.XMLParser:
    """XXE対策済みのXMLパーサ（公文書は信頼済みだが習慣として）。"""
    return etree.XMLParser(resolve_entities=False, no_network=True)


class IngestError(Exception):
    """取り込み失敗（理由を明示してエラーにする。部分登録はしない）。"""


@dataclass
class IngestResult:
    source: str  # 取り込み元の名前（ZIP名・フォルダ名）
    status: str  # ok / skipped / error
    tno: str | None = None
    title: str | None = None
    message: str = ""


# --- [1] ZIP展開 -------------------------------------------------------------


def repair_zip_name(name: str, flag_bits: int) -> str:
    """ZIPエントリ名の cp437 → cp932 修復。

    zipfile はUTF-8フラグ（0x800）のないエントリ名をcp437として復号する。
    e-GovのZIPは日本語名をcp932で格納しているため、フラグがなければ
    cp437バイト列に戻してcp932で読み直す。失敗したらcp437のまま受け入れる。
    """
    if flag_bits & 0x800:  # UTF-8フラグあり → 復号済みで正しい
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    try:
        return raw.decode("cp932")
    except UnicodeDecodeError:
        logger.warning("ZIPエントリ名のcp932修復に失敗（cp437のまま扱う）: %r", name)
        return name


def extract_zip(zip_path: Path, dest: Path) -> None:
    """Zip Slip 対策・ファイル名修復・NFC正規化つきの安全な展開。"""
    dest_resolved = dest.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = repair_zip_name(info.filename, info.flag_bits)
                name = unicodedata.normalize("NFC", name)
                if info.is_dir() or name.endswith("/"):
                    continue
                target = (dest / name).resolve()
                if not target.is_relative_to(dest_resolved):
                    raise IngestError(f"不正なZIPエントリ名です: {info.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile as e:
        raise IngestError(f"ZIPの解凍に失敗しました: {e}") from e


def _find_doc_root(extracted: Path) -> Path:
    """展開結果がフォルダ1つを包んでいる場合は中に降りる（到達番号フォルダ構造）。"""
    current = extracted
    while True:
        entries = [p for p in current.iterdir() if not p.name.startswith(".")]
        if len(entries) == 1 and entries[0].is_dir():
            current = entries[0]
            continue
        return current


# --- [2] 鑑XML特定 -----------------------------------------------------------


def find_kagami(doc_root: Path) -> tuple[Path | None, etree._Element | None]:
    """ルート直下の *.xml から鑑XMLをヒューリスティックに特定する。

    1. ルート要素が DOC で XMLDSig Signature 子要素を持つ
    2. ファイル名が kagami.xml
    3. ファイル名が到達番号らしい数字列（14〜20桁）
    """
    parser = make_xml_parser()
    parsed: list[tuple[Path, etree._Element]] = []
    for p in sorted(doc_root.glob("*.xml")):
        try:
            root = etree.parse(str(p), parser).getroot()
        except etree.XMLSyntaxError:
            continue
        parsed.append((p, root))

    for p, root in parsed:
        if (
            isinstance(root.tag, str)
            and etree.QName(root).localname == "DOC"
            and root.find(_DSIG_SIGNATURE) is not None
        ):
            return p, root
    for p, root in parsed:
        if p.name.lower() == "kagami.xml":
            return p, root
    for p, root in parsed:
        if _TNO_DIGITS_RE.fullmatch(p.stem):
            return p, root
    return None, None


# --- [6] 書庫へ複製 ----------------------------------------------------------


def _move_into_archive(config: Config, doc_root: Path, tno: str) -> Path:
    final = config.docs_dir / tno
    config.docs_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(doc_root, final)  # 同一ボリュームならアトミック
        return final
    except OSError:
        pass
    # 別ボリューム: copy → rename（書庫内の一時名を経由してアトミックに見せる）
    tmp = config.docs_dir / f".tmp-{tno}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        shutil.copytree(doc_root, tmp)
        os.rename(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


# --- [7] カタログ登録 --------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_role(rel_name: str, kagami_rel: str | None, doclink_xmls: set[str]) -> str:
    """拡張子と鑑からの参照関係でファイルの役割を決める（設計 6-5）。"""
    if rel_name == ORIGINAL_ZIP_NAME:
        return "zip"
    if kagami_rel is not None and rel_name == kagami_rel:
        return "kagami"
    suffix = Path(rel_name).suffix.lower()
    if suffix == ".xsl":
        return "xsl"
    if suffix == ".xml" and Path(rel_name).name in doclink_xmls:
        return "yoshiki"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".csv":
        return "csv"
    if suffix == ".dta":  # 届書作成プログラム形式（被保険者データ等）
        return "dta"
    return "other"


def _xml_body_text(path: Path) -> str:
    """検索用にXMLのテキストノードを連結する（Signature部分は除外）。"""
    try:
        root = etree.parse(str(path), make_xml_parser()).getroot()
    except etree.XMLSyntaxError:
        return ""
    parts: list[str] = []
    skip_roots: list[etree._Element] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if any(sr in el.iterancestors() or sr is el for sr in skip_roots):
            continue
        if etree.QName(el).localname == "Signature":
            skip_roots.append(el)
            continue
        if el.text and el.text.strip():
            parts.append(el.text.strip())
    return " ".join(parts)


def _register(
    conn: sqlite3.Connection,
    tno: str,
    meta: Meta,
    pattern_name: str,
    received_date: str | None,
    final_dir: Path,
    kagami_rel: str | None,
    ingested_at: str | None = None,
) -> None:
    doclink_xmls = {
        Path(unicodedata.normalize("NFC", unquote(link))).name
        for link in meta.doclinks
    }
    rows: list[tuple[str, str, str, str]] = []
    body_parts: list[str] = []
    for p in sorted(final_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(final_dir).as_posix())
        role = _classify_role(rel, kagami_rel, doclink_xmls)
        rows.append((tno, rel, role, _sha256(p)))
        if role in ("kagami", "yoshiki"):
            body_parts.append(_xml_body_text(p))
    if ingested_at is None:
        ingested_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with conn:  # 単一トランザクション
        conn.execute(
            "INSERT INTO documents "
            "(tno, received_date, received_raw, agency, title, pattern, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tno,
                received_date,
                meta.received_raw,
                meta.agency,
                meta.title,
                pattern_name,
                ingested_at,
            ),
        )
        conn.executemany(
            "INSERT INTO files (tno, name, role, sha256) VALUES (?, ?, ?, ?)", rows
        )
        conn.execute(
            "INSERT INTO search (tno, title, agency, body) VALUES (?, ?, ?, ?)",
            (tno, meta.title or "", meta.agency or "", " ".join(bp for bp in body_parts if bp)),
        )


# --- [2]〜[4] メタデータ確定（取り込み・再インデックス共通コードパス） --------


def extract_doc_meta(
    doc_root: Path, source_name: str, source_path: Path
) -> tuple[Meta, str, str | None, str | None]:
    """[2] 鑑XML特定 → [3] メタデータ抽出 → [4] 到達番号確定。

    取り込みと再インデックス（NFR-02）で同一のコードパスを共有する。
    戻り値: (meta, pattern_name, received_date, kagami_rel)
    """
    kagami_path, kagami_root = find_kagami(doc_root)
    pattern = match_pattern(kagami_root) if kagami_root is not None else None
    if pattern is not None:
        meta = pattern.extract(kagami_root)
        pattern_name = pattern.name
        received_date = wareki_to_iso(meta.received_raw)
    else:
        # 鑑なし・未知形式: フォールバック登録（形式未対応フラグ相当）
        meta = fallback_meta(f"{doc_root.name} {source_name}", source_path)
        meta.title = source_name
        pattern_name = FALLBACK_PATTERN_NAME
        received_date = meta.received_date
        kagami_path = None

    tno = meta.tno if meta.tno and TNO_RE.fullmatch(meta.tno) else None
    if tno is None:
        m = _TNO_DIGITS_RE.search(doc_root.name) or _TNO_DIGITS_RE.search(source_name)
        tno = m.group(0) if m else f"unknown-{datetime.now():%Y%m%d%H%M%S%f}"
    meta.tno = tno

    kagami_rel = (
        unicodedata.normalize("NFC", kagami_path.relative_to(doc_root).as_posix())
        if kagami_path is not None
        else None
    )
    return meta, pattern_name, received_date, kagami_rel


# --- パイプライン本体 --------------------------------------------------------


def _process(
    config: Config,
    conn: sqlite3.Connection,
    doc_root: Path,
    source_name: str,
    source_path: Path,
    original_zip: Path | None,
) -> IngestResult:
    if not any(p.is_file() for p in doc_root.rglob("*")):
        raise IngestError("ファイルが1つも含まれていません")

    # [2] 鑑XML特定 → [3] メタデータ抽出 → [4] 到達番号確定
    meta, pattern_name, received_date, kagami_rel = extract_doc_meta(
        doc_root, source_name, source_path
    )
    tno = meta.tno

    # [5] 重複チェック（既定はスキップ・上書きしない）
    if (
        conn.execute("SELECT 1 FROM documents WHERE tno = ?", (tno,)).fetchone()
        is not None
        or (config.docs_dir / tno).exists()
    ):
        return IngestResult(
            source=source_name,
            status="skipped",
            tno=tno,
            title=meta.title,
            message="同一の到達番号が取り込み済みのためスキップしました",
        )

    # 元ZIPを文書セット内に複製（ZIP取り込みの場合のみ）
    if original_zip is not None:
        dest = doc_root / ORIGINAL_ZIP_NAME
        if dest.exists():
            raise IngestError(f"文書セット内に {ORIGINAL_ZIP_NAME} が既に存在します")
        shutil.copy2(original_zip, dest)

    # [6] 書庫へ複製 → [7] カタログ登録（失敗したら書庫からも取り除く）
    final_dir = _move_into_archive(config, doc_root, tno)
    try:
        _register(conn, tno, meta, pattern_name, received_date, final_dir, kagami_rel)
    except Exception:
        shutil.rmtree(final_dir, ignore_errors=True)
        raise
    return IngestResult(
        source=source_name,
        status="ok",
        tno=tno,
        title=meta.title,
        message="取り込みました",
    )


def _diagnose_not_zip(path: Path) -> str:
    """ZIPでないファイルの中身を覗いて、ユーザーが対処できるメッセージを返す。"""
    try:
        head = path.read_bytes()[:65536]
    except OSError:
        return "ZIPファイルとして読み取れません"
    text = head.decode("utf-8", errors="replace")
    stripped = text.lstrip("\ufeff \t\r\n").lower()
    if not (stripped.startswith("<!doctype html") or stripped.startswith("<html")):
        return "ZIPファイルとして読み取れません"
    if "e-govアカウントログイン" in text.lower():
        return (
            "ZIPではなく、e-Govのログインページ（HTML）が保存されています。"
            "e-Govのセッションが切れた状態でダウンロードした可能性があります。"
            "e-Govに再ログインし、公文書をダウンロードし直してください"
        )
    return (
        "ZIPではなくHTMLページが保存されています。"
        "ダウンロードが正常に完了していない可能性があるため、"
        "配布元からダウンロードし直してください"
    )


def ingest_zip(config: Config, conn: sqlite3.Connection, zip_path: Path) -> IngestResult:
    """ZIPファイルの取り込み。元ZIPは読み取りのみで変更しない。"""
    source_name = zip_path.name
    try:
        if not zip_path.is_file():
            raise IngestError("ZIPファイルとして読み取れません")
        if not zipfile.is_zipfile(zip_path):
            raise IngestError(_diagnose_not_zip(zip_path))
        workdir = Path(tempfile.mkdtemp(prefix="kobunshoko-ingest-"))
        try:
            extract_root = workdir / "extracted"
            extract_root.mkdir()
            extract_zip(zip_path, extract_root)
            doc_root = _find_doc_root(extract_root)
            return _process(
                config, conn, doc_root, source_name, zip_path, original_zip=zip_path
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except IngestError as e:
        return IngestResult(source=source_name, status="error", message=str(e))
    except Exception as e:  # 予期しない失敗も文書セット単位で握って集計する
        logger.exception("取り込み中に予期しないエラー: %s", zip_path)
        return IngestResult(
            source=source_name, status="error", message=f"予期しないエラー: {e}"
        )


def ingest_folder(
    config: Config, conn: sqlite3.Connection, folder: Path
) -> IngestResult:
    """解凍済みフォルダの取り込み。元フォルダは読み取り専用として複製のみ行う。"""
    source_name = folder.name
    try:
        if not folder.is_dir():
            raise IngestError("フォルダが存在しません")
        workdir = Path(tempfile.mkdtemp(prefix="kobunshoko-ingest-"))
        try:
            doc_root = workdir / (folder.name or "set")
            shutil.copytree(folder, doc_root)
            return _process(
                config, conn, doc_root, source_name, folder, original_zip=None
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except IngestError as e:
        return IngestResult(source=source_name, status="error", message=str(e))
    except Exception as e:
        logger.exception("取り込み中に予期しないエラー: %s", folder)
        return IngestResult(
            source=source_name, status="error", message=f"予期しないエラー: {e}"
        )


# --- 再インデックス（NFR-02） ------------------------------------------------


def reindex(config: Config, conn: sqlite3.Connection) -> list[IngestResult]:
    """カタログを全削除し、書庫 docs/*/ の走査から再構築する。

    メタデータ抽出は取り込みと同一のコードパス（extract_doc_meta → _register）を
    共有する。到達番号はディレクトリ名を主キーとして採用する（D-06）。

    ファイルシステムから再抽出できない取り込み時情報は次の方針で復元する:
    - ingested_at と、fallback文書の件名など取り込み元に由来する値は、
      旧カタログに行が残っていればそこから引き継ぐ（稼働中の再インデックスで
      メタデータが取り込み時と乖離しないようにする）
    - カタログ全損時の fallback 文書の日付は、元ZIPのmtimeを保存している
      original.zip（copy2で複製）から復元する
    - カタログ全損時の ingested_at はディレクトリの ctime（書庫へ移した時刻）で
      代用する。mtime はフォルダ取り込み時に copytree が元フォルダの更新日時を
      保存するため、取込日時の代用には使えない
    """
    preserved: dict[str, sqlite3.Row] = {
        row["tno"]: row
        for row in conn.execute(
            "SELECT tno, received_date, received_raw, agency, title, pattern, "
            "ingested_at FROM documents"
        )
    }
    db.clear_catalog(conn)
    results: list[IngestResult] = []
    if not config.docs_dir.is_dir():
        return results
    for doc_dir in sorted(config.docs_dir.iterdir()):
        if not doc_dir.is_dir() or doc_dir.name.startswith("."):
            continue
        source_name = doc_dir.name
        try:
            if not TNO_RE.fullmatch(doc_dir.name):
                raise IngestError("ディレクトリ名が到達番号として不正です")
            if not any(p.is_file() for p in doc_dir.rglob("*")):
                raise IngestError("ファイルが1つも含まれていません")
            original_zip = doc_dir / ORIGINAL_ZIP_NAME
            source_path = original_zip if original_zip.is_file() else doc_dir
            meta, pattern_name, received_date, kagami_rel = extract_doc_meta(
                doc_dir, source_name, source_path
            )
            tno = doc_dir.name  # ディレクトリ名が主キー
            meta.tno = tno
            old = preserved.get(tno)
            if old is not None:
                ingested_at = old["ingested_at"]
                if (
                    pattern_name == FALLBACK_PATTERN_NAME
                    and old["pattern"] == FALLBACK_PATTERN_NAME
                ):
                    # fallback文書の件名・日付は取り込み元（ZIP/フォルダ）の
                    # 名前と更新日時に由来し、書庫からは完全には再抽出できない
                    meta.title = old["title"]
                    meta.received_raw = old["received_raw"]
                    meta.agency = old["agency"]
                    received_date = old["received_date"]
            else:
                ingested_at = (
                    datetime.fromtimestamp(doc_dir.stat().st_ctime)
                    .astimezone()
                    .isoformat(timespec="seconds")
                )
            _register(
                conn, tno, meta, pattern_name, received_date, doc_dir, kagami_rel,
                ingested_at=ingested_at,
            )
            results.append(
                IngestResult(
                    source=source_name,
                    status="ok",
                    tno=tno,
                    title=meta.title,
                    message="再構築しました",
                )
            )
        except IngestError as e:
            results.append(
                IngestResult(source=source_name, status="error", message=str(e))
            )
        except Exception as e:
            logger.exception("再インデックス中に予期しないエラー: %s", doc_dir)
            results.append(
                IngestResult(
                    source=source_name, status="error", message=f"予期しないエラー: {e}"
                )
            )
    return results


# --- 過去分の一括取り込み: 検出（FR-07） -------------------------------------


@dataclass
class ScanCandidate:
    """走査で見つかった「e-Gov公文書らしい」ZIP・フォルダ。"""

    path: str  # 絶対パス
    kind: str  # zip / folder
    tno: str | None  # 推定到達番号（不明ならNone）
    ingested: bool  # 既に取り込み済みか


def is_kobunshoko_zip(zip_path: Path) -> bool:
    """ZIPが公文書らしいか（監視フォルダの自動取り込み判定などに使う）。"""
    return _zip_candidate(zip_path)[0]


def _zip_candidate(zip_path: Path) -> tuple[bool, str | None]:
    """ZIPが公文書らしいか判定する。名前の到達番号 → 中身の鑑XMLの順に見る。"""
    m = _TNO_DIGITS_RE.search(zip_path.name)
    if m:
        return True, m.group(0)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = unicodedata.normalize(
                    "NFC", repair_zip_name(info.filename, info.flag_bits)
                )
                inner = Path(name)
                if inner.suffix.lower() != ".xml":
                    continue
                if _TNO_DIGITS_RE.fullmatch(inner.stem):
                    return True, inner.stem
                if inner.name.lower() == "kagami.xml":
                    return True, None
    except (OSError, zipfile.BadZipFile):
        return False, None
    return False, None


def _folder_candidate(folder: Path) -> tuple[bool, str | None]:
    """フォルダが公文書らしいか判定する（鑑XMLヒューリスティックを流用）。"""
    try:
        kagami_path, _ = find_kagami(folder)
    except OSError:
        return False, None
    if kagami_path is None:
        return False, None
    m = _TNO_DIGITS_RE.search(folder.name) or _TNO_DIGITS_RE.search(kagami_path.stem)
    return True, m.group(0) if m else None


def scan_directory(
    config: Config, conn: sqlite3.Connection, base: Path
) -> list[ScanCandidate]:
    """指定ディレクトリを走査し、公文書らしいZIP・フォルダを検出する（FR-07）。

    - 隠しファイル・シンボリックリンク・書庫自身は対象外
    - 公文書らしいフォルダを見つけたら、その中へは降りない
    """
    archive = config.archive.resolve()
    candidates: list[ScanCandidate] = []

    def walk(d: Path) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for p in entries:
            if p.name.startswith(".") or p.is_symlink():
                continue
            resolved = p.resolve()
            if resolved == archive or resolved.is_relative_to(archive):
                continue  # 書庫自身を再取り込みしない
            if p.is_file():
                if p.suffix.lower() == ".zip" and zipfile.is_zipfile(p):
                    ok, tno = _zip_candidate(p)
                    if ok:
                        candidates.append(
                            ScanCandidate(path=str(p), kind="zip", tno=tno,
                                          ingested=_is_ingested(config, conn, tno))
                        )
            elif p.is_dir():
                ok, tno = _folder_candidate(p)
                if ok:
                    candidates.append(
                        ScanCandidate(path=str(p), kind="folder", tno=tno,
                                      ingested=_is_ingested(config, conn, tno))
                    )
                else:
                    walk(p)  # 公文書でないフォルダはサブフォルダも走査する

    walk(base.expanduser().resolve())
    return candidates


def _is_ingested(config: Config, conn: sqlite3.Connection, tno: str | None) -> bool:
    if not tno:
        return False
    row = conn.execute("SELECT 1 FROM documents WHERE tno = ?", (tno,)).fetchone()
    return row is not None or (config.docs_dir / tno).exists()
