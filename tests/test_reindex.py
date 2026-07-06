"""再インデックス（NFR-02・受け入れ基準7）のテスト。

catalog.db を削除しても、POST /reindex による書庫走査だけで
一覧・検索・閲覧・役割判定が完全に復元されることを確認する。
再構築は取り込みと同一の抽出コードパスを共有する。
"""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from urllib.parse import quote

from conftest import DOCNO_TNO, FIXTURES, TOTATUNO_TNO

YOSHIKI_XML = f"テスト通知書_令和8年1月送付分({DOCNO_TNO}).xml"


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


def _snapshot(config):
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    try:
        docs = sorted(
            tuple(r)
            for r in conn.execute(
                "SELECT tno, received_date, received_raw, agency, title, pattern "
                "FROM documents"
            )
        )
        files = sorted(
            tuple(r)
            for r in conn.execute("SELECT tno, name, role, sha256 FROM files")
        )
        search = sorted(
            tuple(r)
            for r in conn.execute("SELECT tno, title, agency, body FROM search")
        )
        return docs, files, search
    finally:
        conn.close()


def test_reindex_restores_catalog_after_db_loss(client, config, tmp_path, totatuno_zip):
    # ZIP・フォルダの2形式を取り込んでおく
    _upload(client, totatuno_zip)
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    client.post("/ingest/path", data={"path": str(src)})

    before = _snapshot(config)
    assert len(before[0]) == 2

    # カタログ全損 → 再インデックス
    config.db_path.unlink()
    res = client.post("/reindex")
    assert res.status_code == 200
    assert "再インデックス結果" in res.text
    assert res.text.count("再構築しました") == 2

    # documents / files / search がすべて同一内容で復元される
    assert _snapshot(config) == before

    # 一覧・検索・閲覧の復元（受け入れ基準7）
    res = client.get("/")
    assert TOTATUNO_TNO in res.text and DOCNO_TNO in res.text
    res = client.get("/", params={"q": "審査結果"})
    assert TOTATUNO_TNO in res.text and DOCNO_TNO not in res.text
    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    assert "電子申請に対する審査結果について" in res.text


def test_reindex_is_idempotent_and_clears_stale_rows(client, config, totatuno_zip):
    _upload(client, totatuno_zip)

    # 書庫にない文書がDBに残っていても、再構築後は消える（全削除→走査）
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    with conn:
        conn.execute(
            "INSERT INTO documents (tno, title, pattern, ingested_at) "
            "VALUES ('99999999999999', 'ゴースト', 'fallback', '2026-01-01T00:00:00')"
        )
    conn.close()

    res = client.post("/reindex")
    assert res.status_code == 200
    res = client.get("/")
    assert TOTATUNO_TNO in res.text
    assert "ゴースト" not in res.text

    # 2回実行しても結果は同じ
    docs1 = _snapshot(config)
    client.post("/reindex")
    assert _snapshot(config) == docs1


def test_reindex_preserves_roles_and_fallback_flag(client, config, tmp_path, totatuno_zip):
    _upload(client, totatuno_zip)
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    client.post("/ingest/path", data={"path": str(src)})
    # 形式未対応（fallback）文書
    mystery = tmp_path / "mystery_20990101012345678"
    mystery.mkdir()
    (mystery / "notice.xml").write_text(
        "<?xml version='1.0'?><NOTICE><SUBJECT>謎の通知</SUBJECT></NOTICE>",
        encoding="utf-8",
    )
    client.post("/ingest/path", data={"path": str(mystery)})

    client.post("/reindex")

    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    try:
        roles = {
            r["name"]: r["role"]
            for r in conn.execute(
                "SELECT name, role FROM files WHERE tno=?", (DOCNO_TNO,)
            )
        }
        # DOCLINK参照の再抽出により yoshiki 判定が復元される
        assert roles[YOSHIKI_XML] == "yoshiki"
        assert roles[f"{DOCNO_TNO}.xml"] == "kagami"
        # ZIP取り込み分は original.zip の役割も復元される
        zip_role = conn.execute(
            "SELECT role FROM files WHERE tno=? AND name='original.zip'",
            (TOTATUNO_TNO,),
        ).fetchone()
        assert zip_role["role"] == "zip"
        patterns = {
            r["tno"]: r["pattern"]
            for r in conn.execute("SELECT tno, pattern FROM documents")
        }
        assert patterns["20990101012345678"] == "fallback"
        assert patterns[TOTATUNO_TNO] == "totatuno"
        assert patterns[DOCNO_TNO] == "docno"
    finally:
        conn.close()

    res = client.get("/")
    assert "形式未対応" in res.text


MYSTERY_TNO = "20990101012345678"
MYSTERY_ZIP = f"mystery_{MYSTERY_TNO}.zip"
FOLDER_TNO = "20990101099999999"


def _make_mystery_zip(tmp_path, mtime_iso="2026-01-15T09:00:00"):
    """鑑なし（fallback）ZIP。元ZIPのmtimeを過去日付に設定する。"""
    dest = tmp_path / MYSTERY_ZIP
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(
            "notice.xml",
            "<?xml version='1.0'?><NOTICE><SUBJECT>謎の通知</SUBJECT></NOTICE>",
        )
    ts = datetime.fromisoformat(mtime_iso).timestamp()
    os.utime(dest, (ts, ts))
    return dest


def _doc_row(config, tno):
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    try:
        return conn.execute(
            "SELECT title, received_date, received_raw, agency, pattern, "
            "ingested_at FROM documents WHERE tno = ?",
            (tno,),
        ).fetchone()
    finally:
        conn.close()


def test_reindex_keeps_fallback_metadata_when_db_intact(client, config, tmp_path):
    """DBが生きている状態の再インデックスは、fallback文書のメタデータ
    （ZIP名由来の件名・元ZIP mtime由来の受付日・取込日時）を変えない。"""
    zip_path = _make_mystery_zip(tmp_path)
    res = client.post("/ingest/path", data={"path": str(zip_path)})
    assert "成功" in res.text

    before = _doc_row(config, MYSTERY_TNO)
    assert before["title"] == MYSTERY_ZIP  # fallbackの件名=ZIP名
    assert before["received_date"] == "2026-01-15"  # 日付=元ZIPの更新日時
    assert before["pattern"] == "fallback"

    res = client.post("/reindex")
    assert res.status_code == 200
    after = _doc_row(config, MYSTERY_TNO)
    assert tuple(after) == tuple(before)  # ingested_at 含めて乖離しない


def test_reindex_after_db_loss_restores_fallback_dates_from_filesystem(
    client, config, tmp_path
):
    """カタログ全損後の再構築でも、fallback文書の受付日はファイルシステムに
    残る情報（original.zip のmtime・複製時に保存された元フォルダのmtime）から
    復元し、取込日時が元フォルダの更新日時へ巻き戻らない。"""
    # ZIP取り込み（元ZIP mtime=2026-01-15）
    zip_path = _make_mystery_zip(tmp_path)
    client.post("/ingest/path", data={"path": str(zip_path)})
    # フォルダ取り込み（元フォルダ mtime=2025-03-01）
    folder = tmp_path / f"old_folder_{FOLDER_TNO}"
    folder.mkdir()
    (folder / "notice.xml").write_text(
        "<?xml version='1.0'?><NOTICE><SUBJECT>古い通知</SUBJECT></NOTICE>",
        encoding="utf-8",
    )
    old_ts = datetime.fromisoformat("2025-03-01T10:00:00").timestamp()
    os.utime(folder, (old_ts, old_ts))
    res = client.post("/ingest/path", data={"path": str(folder)})
    assert "成功" in res.text

    today = datetime.now().date().isoformat()

    # カタログ全損 → 再インデックス
    config.db_path.unlink()
    res = client.post("/reindex")
    assert res.status_code == 200
    assert res.text.count("再構築しました") == 2

    # ZIP分: received_date は original.zip のmtime（copy2で保存）から復元される
    zip_doc = _doc_row(config, MYSTERY_TNO)
    assert zip_doc["received_date"] == "2026-01-15"
    assert zip_doc["pattern"] == "fallback"
    # フォルダ分: received_date は複製時に保存された元フォルダのmtime
    folder_doc = _doc_row(config, FOLDER_TNO)
    assert folder_doc["received_date"] == "2025-03-01"
    # ingested_at は元フォルダのmtime（過去）に巻き戻らない
    assert zip_doc["ingested_at"] > today
    assert folder_doc["ingested_at"] > today
    assert not folder_doc["ingested_at"].startswith("2025-03")


def test_reindex_unit_uses_directory_name_as_tno(config, totatuno_zip):
    """reindex は docs/ のディレクトリ名を主キーとして再登録する。"""
    from kobunshoko import db as db_mod
    from kobunshoko import ingest as ingest_mod

    conn = db_mod.connect(config.db_path)
    db_mod.ensure_schema(conn)
    ingest_mod.ingest_zip(config, conn, totatuno_zip)

    results = ingest_mod.reindex(config, conn)
    assert [r.status for r in results] == ["ok"]
    assert results[0].tno == TOTATUNO_TNO
    conn.close()
