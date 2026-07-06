"""改ざん検知（FR-06）のテスト。

鑑XMLの XMLDSig Reference（URLエンコード済みURIを含む）と実ファイルの
SHA-256照合、POST /doc/{tno}/verify、詳細画面の ok/mismatch/unverifiable
バッジ表示を確認する。
"""

from __future__ import annotations

import shutil

from conftest import DOCNO_TNO, FIXTURES, TOTATUNO_TNO

KAGAMI_XML = f"{TOTATUNO_TNO}.xml"
PDF_NAME = "01_控え文書.pdf"
DOCNO_CSV = f"テスト通知書_令和8年1月送付分_明細({DOCNO_TNO}).csv"


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


def _db_rows(config, sql, params=()):
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --- 単体: check_references ---------------------------------------------------


def test_check_references_ok_with_url_encoded_uri(tmp_path):
    from kobunshoko.verify import check_references

    d = tmp_path / "set"
    shutil.copytree(FIXTURES / "totatuno_set", d)
    result = check_references(d / KAGAMI_XML, d)
    assert result.status == "ok"
    # URLエンコード済みURI（01_%E6%8E%A7...）がデコードされてPDFに解決される
    file_refs = {r.name: r.ok for r in result.refs if r.name is not None}
    assert file_refs == {PDF_NAME: True}
    # 同一文書内参照（#DOCBODY）は照合対象外
    same_doc = [r for r in result.refs if r.uri.startswith("#")]
    assert same_doc and all(r.ok is None for r in same_doc)


def test_check_references_mismatch_on_tampered_file(tmp_path):
    from kobunshoko.verify import check_references

    d = tmp_path / "set"
    shutil.copytree(FIXTURES / "totatuno_set", d)
    with open(d / PDF_NAME, "ab") as f:
        f.write(b"tampered")
    result = check_references(d / KAGAMI_XML, d)
    assert result.status == "mismatch"
    assert any(r.name == PDF_NAME and r.ok is False for r in result.refs)


def test_check_references_missing_file_is_unverifiable(tmp_path):
    from kobunshoko.verify import check_references

    d = tmp_path / "set"
    shutil.copytree(FIXTURES / "totatuno_set", d)
    (d / PDF_NAME).unlink()
    result = check_references(d / KAGAMI_XML, d)
    assert result.status == "unverifiable"


def test_check_references_without_signature_is_unverifiable(tmp_path):
    from kobunshoko.verify import check_references

    p = tmp_path / "k.xml"
    p.write_text("<?xml version='1.0'?><DOC><BODY>x</BODY></DOC>", encoding="utf-8")
    result = check_references(p, tmp_path)
    assert result.status == "unverifiable"
    assert result.refs == []


def test_check_references_rejects_path_escape(tmp_path):
    """文書セット外を指すReference URIは照合不能として扱う（配信はしない）。"""
    from kobunshoko.verify import check_references

    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")
    d = tmp_path / "set"
    d.mkdir()
    p = d / "k.xml"
    p.write_text(
        "<?xml version='1.0'?><DOC>"
        '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>'
        '<Reference URI="../secret.txt">'
        '<DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
        "<DigestValue>AAAA</DigestValue></Reference>"
        "</SignedInfo></Signature></DOC>",
        encoding="utf-8",
    )
    result = check_references(p, d)
    assert result.status == "unverifiable"
    assert all(r.ok is not True for r in result.refs)


# --- 結合: POST /doc/{tno}/verify とバッジ表示 --------------------------------


def test_verify_endpoint_ok(client, config, totatuno_zip):
    _upload(client, totatuno_zip)

    res = client.post(f"/doc/{TOTATUNO_TNO}/verify", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == f"/doc/{TOTATUNO_TNO}"

    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "改ざんなし" in res.text

    rows = _db_rows(
        config, "SELECT verify_status FROM documents WHERE tno=?", (TOTATUNO_TNO,)
    )
    assert rows[0]["verify_status"] == "ok"
    rows = _db_rows(
        config,
        "SELECT digest_ok FROM files WHERE tno=? AND name=?",
        (TOTATUNO_TNO, PDF_NAME),
    )
    assert rows[0]["digest_ok"] == 1


def test_verify_endpoint_mismatch_on_tampered_archive(client, config, totatuno_zip):
    _upload(client, totatuno_zip)
    # 書庫内の実ファイルを改ざん
    target = config.docs_dir / TOTATUNO_TNO / PDF_NAME
    with open(target, "ab") as f:
        f.write(b"tampered")

    res = client.post(f"/doc/{TOTATUNO_TNO}/verify")
    assert res.status_code == 200
    assert "不一致" in res.text  # 詳細画面のバッジ

    rows = _db_rows(
        config,
        "SELECT digest_ok FROM files WHERE tno=? AND name=?",
        (TOTATUNO_TNO, PDF_NAME),
    )
    assert rows[0]["digest_ok"] == 0


def test_verify_docno_multiple_file_refs(client, config, tmp_path):
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    client.post("/ingest/path", data={"path": str(src)})

    res = client.post(f"/doc/{DOCNO_TNO}/verify")
    assert "改ざんなし" in res.text
    # 日本語名（非エンコード）のCSV参照とXSL参照の両方が照合される
    rows = _db_rows(
        config,
        "SELECT name, digest_ok FROM files WHERE tno=? AND digest_ok IS NOT NULL",
        (DOCNO_TNO,),
    )
    checked = {r["name"]: r["digest_ok"] for r in rows}
    assert checked == {"yoshiki_29_test_001.xsl": 1, DOCNO_CSV: 1}


def test_verify_fallback_doc_is_unverifiable(client, tmp_path):
    src = tmp_path / "mystery_20990101012345678"
    src.mkdir()
    (src / "notice.xml").write_text(
        "<?xml version='1.0'?><NOTICE><SUBJECT>謎の通知</SUBJECT></NOTICE>",
        encoding="utf-8",
    )
    client.post("/ingest/path", data={"path": str(src)})

    res = client.post("/doc/20990101012345678/verify")
    assert res.status_code == 200
    assert "検証不能" in res.text


def test_reverify_clears_stale_digest_ok_for_missing_file(client, config, totatuno_zip):
    """再照合で照合不能になったファイルに、前回の digest_ok（一致）が残留しない。"""
    _upload(client, totatuno_zip)
    client.post(f"/doc/{TOTATUNO_TNO}/verify")
    rows = _db_rows(
        config,
        "SELECT digest_ok FROM files WHERE tno=? AND name=?",
        (TOTATUNO_TNO, PDF_NAME),
    )
    assert rows[0]["digest_ok"] == 1

    # 書庫上でPDFが失われた状態で再照合
    (config.docs_dir / TOTATUNO_TNO / PDF_NAME).unlink()
    res = client.post(f"/doc/{TOTATUNO_TNO}/verify")
    assert res.status_code == 200
    assert "検証不能" in res.text

    rows = _db_rows(
        config,
        "SELECT digest_ok FROM files WHERE tno=? AND name=?",
        (TOTATUNO_TNO, PDF_NAME),
    )
    assert rows[0]["digest_ok"] is None  # 前回の「一致」がリセットされる
    # 詳細画面でも存在しないファイルに一致バッジが出ない
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert '<span class="badge ok">一致</span>' not in res.text


def test_verify_unknown_tno_is_404(client):
    res = client.post("/doc/00000000000000/verify", follow_redirects=False)
    assert res.status_code == 404
    res = client.post("/doc/../etc/verify", follow_redirects=False)
    assert res.status_code in (404, 307)  # パス正規化後も404系で拒否


def test_detail_shows_unverified_badge_before_verify(client, totatuno_zip):
    _upload(client, totatuno_zip)
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "未検証" in res.text
    assert f"/doc/{TOTATUNO_TNO}/verify" in res.text  # 照合実行ボタン
