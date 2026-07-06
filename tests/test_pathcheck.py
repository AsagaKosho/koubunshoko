"""パス検証（設計 §9-1）の単体テスト。"""

import unicodedata

import pytest
from fastapi import HTTPException

from kobunshoko import db
from kobunshoko.main import resolve_file


@pytest.fixture()
def setup(config):
    """docs/{tno}/ に登録済みファイルと未登録ファイルを用意する。"""
    tno = "20990101010000001"
    doc_dir = config.docs_dir / tno
    doc_dir.mkdir(parents=True)
    (doc_dir / "鑑.xml").write_text("<DOC/>", encoding="utf-8")
    (doc_dir / "stray.txt").write_text("not registered", encoding="utf-8")
    (config.archive / "catalog.db").touch()
    conn = db.connect(config.db_path)
    db.ensure_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO documents (tno, ingested_at) VALUES (?, '2026-07-06T00:00:00')",
            (tno,),
        )
        conn.execute(
            "INSERT INTO files (tno, name, role, sha256) VALUES (?, ?, 'kagami', 'x')",
            (tno, "鑑.xml"),
        )
    yield config, conn, tno
    conn.close()


def _status(config, conn, tno, name):
    with pytest.raises(HTTPException) as exc:
        resolve_file(config, conn, tno, name)
    return exc.value.status_code


def test_registered_file_resolves(setup):
    config, conn, tno = setup
    row, path = resolve_file(config, conn, tno, "鑑.xml")
    assert row["role"] == "kagami"
    assert path.is_file()


def test_nfd_name_resolves_via_nfc(setup):
    # macOSのFS由来でNFD化されたURLでも、NFC正規化してDB照合する
    config, conn, tno = setup
    nfd = unicodedata.normalize("NFD", "鑑.xml")
    row, _ = resolve_file(config, conn, tno, nfd)
    assert row["name"] == "鑑.xml"


def test_traversal_rejected(setup):
    config, conn, tno = setup
    assert _status(config, conn, tno, "../catalog.db") == 404
    assert _status(config, conn, tno, "../../etc/passwd") == 404
    assert _status(config, conn, tno, "a/../../catalog.db") == 404


def test_absolute_path_rejected(setup):
    config, conn, tno = setup
    assert _status(config, conn, tno, "/etc/passwd") == 404


def test_bad_tno_rejected(setup):
    config, conn, _ = setup
    assert _status(config, conn, "../docs", "鑑.xml") == 404
    assert _status(config, conn, "a/b", "鑑.xml") == 404
    assert _status(config, conn, "", "鑑.xml") == 404


def test_unregistered_file_rejected(setup):
    # 書庫内に実在してもDB未登録なら配信しない
    config, conn, tno = setup
    assert _status(config, conn, tno, "stray.txt") == 404


def test_unknown_document_rejected(setup):
    config, conn, _ = setup
    assert _status(config, conn, "20990101019999999", "鑑.xml") == 404
