"""取り込み→一覧→閲覧の結合テスト（受け入れ基準 1, 5, 6 相当）。

FastAPI TestClient で ZIPアップロード / パス指定取り込み / 重複スキップ /
元ファイル無変更 / XSLT閲覧 / CSVテーブル / PDF・原本配信を通しで確認する。
"""

from __future__ import annotations

import hashlib
import shutil
from urllib.parse import quote

from conftest import DOCNO_TNO, FIXTURES, TOTATUNO_TNO

KAGAMI_CSV = f"テスト通知書_令和8年1月送付分_明細({DOCNO_TNO}).csv"
YOSHIKI_XML = f"テスト通知書_令和8年1月送付分({DOCNO_TNO}).xml"


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


def test_zip_upload_to_view_flow(client, config, totatuno_zip):
    before = hashlib.sha256(totatuno_zip.read_bytes()).hexdigest()

    # --- 取り込み（cp437名修復が必要なZIP） ---
    res = _upload(client, totatuno_zip)
    assert res.status_code == 200
    assert "成功" in res.text
    assert TOTATUNO_TNO in res.text

    # 元ZIPは無変更（FR-01）
    assert hashlib.sha256(totatuno_zip.read_bytes()).hexdigest() == before

    # 書庫に原本一式＋original.zipが平置きされている
    doc_dir = config.docs_dir / TOTATUNO_TNO
    assert (doc_dir / "original.zip").is_file()
    assert (doc_dir / "鑑文書表示用スタイルシート.xsl").is_file()  # cp437修復済みの名前
    assert (doc_dir / "01_控え文書.pdf").is_file()

    # --- 一覧（FR-03） ---
    res = client.get("/")
    assert res.status_code == 200
    assert "架空労働基準監督署" in res.text
    assert "電子申請に対する審査結果について" in res.text
    assert "2026-06-01" in res.text  # 和暦→西暦

    # --- 詳細 ---
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert res.status_code == 200
    assert "01_控え文書.pdf" in res.text
    assert "srcdoc=" in res.text  # 鑑の埋め込み表示

    # --- 鑑XMLのXSLT閲覧（FR-04） ---
    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    assert "電子申請に対する審査結果について" in res.text
    assert "株式会社テスト商事" in res.text
    assert "簡易表示" not in res.text  # 変換成功（フォールバックしていない）
    assert "shift_jis" not in res.text.lower()  # UTF-8に正規化されている
    assert 'sandbox="allow-popups' in res.text  # iframe sandbox

    # --- PDF閲覧・原本配信 ---
    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote('01_控え文書.pdf')}")
    assert res.status_code == 200
    res = client.get(f"/doc/{TOTATUNO_TNO}/raw/{quote('01_控え文書.pdf')}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "inline" in res.headers["content-disposition"]
    assert res.content == (FIXTURES / "totatuno_set" / "01_控え文書.pdf").read_bytes()

    # XML原本は text/plain で、バイト列は原本と完全一致（D-04）
    res = client.get(f"/doc/{TOTATUNO_TNO}/raw/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert res.content == (FIXTURES / "totatuno_set" / f"{TOTATUNO_TNO}.xml").read_bytes()


def test_duplicate_zip_is_skipped(client, totatuno_zip):
    assert "成功" in _upload(client, totatuno_zip).text
    res = _upload(client, totatuno_zip)
    assert res.status_code == 200
    assert "スキップ" in res.text
    # 一覧上は1件のまま
    res = client.get("/")
    assert res.text.count(TOTATUNO_TNO) >= 1


def test_folder_ingest_and_csv_view(client, config, tmp_path):
    # 解凍済みフォルダのパス指定取り込み（FR-01）
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    snapshot = sorted(
        (p.relative_to(src).as_posix(), p.read_bytes())
        for p in src.rglob("*") if p.is_file()
    )

    res = client.post("/ingest/path", data={"path": str(src)})
    assert res.status_code == 200
    assert "成功" in res.text
    assert DOCNO_TNO in res.text

    # 取り込み元フォルダは無変更（FR-01）
    after = sorted(
        (p.relative_to(src).as_posix(), p.read_bytes())
        for p in src.rglob("*") if p.is_file()
    )
    assert after == snapshot
    # フォルダ取り込みでは original.zip は作られない
    assert not (config.docs_dir / DOCNO_TNO / "original.zip").exists()

    # 一覧: docnoパターンのメタデータ
    res = client.get("/")
    assert "架空年金機構" in res.text
    assert "2026-01-22" in res.text  # 全角数字の和暦→西暦

    # 鑑のXSLT閲覧
    res = client.get(f"/doc/{DOCNO_TNO}/view/{quote(f'{DOCNO_TNO}.xml')}")
    assert res.status_code == 200
    assert "架空年金機構からのお知らせ" in res.text

    # 様式XML（yoshiki）のXSLT閲覧
    res = client.get(f"/doc/{DOCNO_TNO}/view/{quote(YOSHIKI_XML)}")
    assert res.status_code == 200
    assert "テスト通知書（合成テストデータ）" in res.text
    assert "株式会社　テスト商事" in res.text

    # CSV: Shift_JISをUTF-8化してテーブル表示。<br/>断片はエスケープされて残る
    res = client.get(f"/doc/{DOCNO_TNO}/view/{quote(KAGAMI_CSV)}")
    assert res.status_code == 200
    assert "機構からのお知らせ" in res.text
    assert "99ﾃｽﾄ" in res.text
    assert "&lt;br/&gt;" in res.text  # HTMLとして解釈させない
    assert "<br/>詳細はダミー" not in res.text

    # 役割判定: 鑑DOCLINKから参照されるXMLは yoshiki
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    roles = {
        r["name"]: r["role"]
        for r in conn.execute("SELECT name, role FROM files WHERE tno=?", (DOCNO_TNO,))
    }
    conn.close()
    assert roles[f"{DOCNO_TNO}.xml"] == "kagami"
    assert roles[YOSHIKI_XML] == "yoshiki"
    assert roles[KAGAMI_CSV] == "csv"
    assert roles["kagami.xsl"] == "xsl"


def test_missing_xsl_falls_back_to_tree_view(client, tmp_path):
    src = tmp_path / TOTATUNO_TNO
    shutil.copytree(FIXTURES / "totatuno_set", src)
    (src / "鑑文書表示用スタイルシート.xsl").unlink()

    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text

    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    assert "簡易表示" in res.text  # フォールバックの明示
    assert "TOTATUNO" in res.text  # 要素名と値のツリー表示
    assert "架空労働基準監督署" in res.text


def test_unknown_schema_falls_back_registration(client, config, tmp_path):
    src = tmp_path / "mystery_20990101012345678"
    src.mkdir()
    (src / "notice.xml").write_text(
        "<?xml version='1.0'?><NOTICE><SUBJECT>謎の通知</SUBJECT></NOTICE>",
        encoding="utf-8",
    )
    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text

    res = client.get("/")
    assert "形式未対応" in res.text  # fallbackフラグの表示
    assert "20990101012345678" in res.text


def test_broken_zip_reports_error_without_trace(client, config, tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"this is not a zip")
    res = _upload(client, bad)
    assert res.status_code == 200
    assert "失敗" in res.text
    assert "ZIPファイルとして読み取れません" in res.text
    # 書庫・DBに痕跡なし（all-or-nothing）
    assert list(config.docs_dir.iterdir()) == []
    res = client.get("/")
    assert "まだ文書がありません" in res.text


def test_egov_login_page_saved_as_zip_gets_guidance(client, config, tmp_path):
    # e-Govセッション切れ時、ログインページHTMLが .zip 名で保存される実事例
    bad = tmp_path / "209912019912345678_20260706185723.zip"
    bad.write_bytes(
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        "<title>e-Govアカウントログイン</title></head>"
        "<body>メールアドレス パスワード</body></html>".encode("utf-8")
    )
    res = _upload(client, bad)
    assert res.status_code == 200
    assert "e-Govのログインページ" in res.text
    assert "再ログイン" in res.text
    assert list(config.docs_dir.iterdir()) == []


def test_generic_html_saved_as_zip_gets_guidance(client, config, tmp_path):
    bad = tmp_path / "notice.zip"
    # BOM・前置空白つきの <html> 開始も検知する
    bad.write_bytes("\ufeff \n<HTML><body>error</body></HTML>".encode("utf-8"))
    res = _upload(client, bad)
    assert res.status_code == 200
    assert "ZIPではなくHTMLページ" in res.text
    assert "ダウンロードし直してください" in res.text
    assert list(config.docs_dir.iterdir()) == []


def test_list_filters_and_sort(client, totatuno_zip, docno_zip):
    _upload(client, totatuno_zip)
    _upload(client, docno_zip)

    res = client.get("/", params={"agency": "架空年金機構"})
    assert DOCNO_TNO in res.text
    assert TOTATUNO_TNO not in res.text

    res = client.get("/", params={"month": "2026-06"})
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO not in res.text

    res = client.get("/", params={"sort": "ingested"})
    assert res.status_code == 200
    # 取込日順: 後から取り込んだ docno が先に出る
    assert res.text.index(DOCNO_TNO) < res.text.index(TOTATUNO_TNO)

    res = client.get("/", params={"sort": "received"})
    # 受付日順: 2026-06-01 (totatuno) が 2026-01-22 (docno) より先
    assert res.text.index(TOTATUNO_TNO) < res.text.index(DOCNO_TNO)
