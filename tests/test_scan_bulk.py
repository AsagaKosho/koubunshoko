"""過去分の一括取り込み（FR-07）のテスト。

GET /scan?dir=... で公文書らしいZIP・フォルダを検出して一覧提示し、
POST /ingest/bulk で選択分を取り込む流れを確認する。
"""

from __future__ import annotations

import shutil
import zipfile

from conftest import DOCNO_TNO, FIXTURES, TOTATUNO_TNO


def _make_scanroot(tmp_path, totatuno_zip):
    """公文書2件＋おとり数点を含む走査対象ディレクトリを作る。"""
    root = tmp_path / "scanroot"
    sub = root / "sub"
    sub.mkdir(parents=True)
    # 候補1: サブフォルダ内の公文書ZIP（名前に到達番号）
    zip_dest = sub / totatuno_zip.name
    shutil.copy(totatuno_zip, zip_dest)
    # 候補2: 解凍済み公文書フォルダ
    folder_dest = root / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", folder_dest)
    # おとり: 公文書でないZIP・テキスト・フォルダ
    with zipfile.ZipFile(root / "photos.zip", "w") as zf:
        zf.writestr("a.txt", "not a kobunshoko")
    (root / "decoy.txt").write_text("x", encoding="utf-8")
    plain = root / "plain_folder"
    plain.mkdir()
    (plain / "readme.txt").write_text("x", encoding="utf-8")
    # 隠しフォルダは走査対象外
    hidden = root / ".hidden"
    hidden.mkdir()
    shutil.copy(totatuno_zip, hidden / "hidden.zip")
    return root, zip_dest, folder_dest


def test_scan_detects_candidates(client, tmp_path, totatuno_zip):
    root, zip_dest, folder_dest = _make_scanroot(tmp_path, totatuno_zip)

    res = client.get("/scan", params={"dir": str(root)})
    assert res.status_code == 200
    assert str(zip_dest) in res.text
    assert str(folder_dest) in res.text
    assert "photos.zip" not in res.text
    assert "plain_folder" not in res.text
    assert ".hidden" not in res.text
    assert "未取り込み" in res.text
    # 推定到達番号の表示
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO in res.text


def test_bulk_ingest_selected(client, config, tmp_path, totatuno_zip):
    root, zip_dest, folder_dest = _make_scanroot(tmp_path, totatuno_zip)

    res = client.post(
        "/ingest/bulk", data={"paths": [str(zip_dest), str(folder_dest)]}
    )
    assert res.status_code == 200
    assert res.text.count("成功") == 2
    assert (config.docs_dir / TOTATUNO_TNO).is_dir()
    assert (config.docs_dir / DOCNO_TNO).is_dir()

    # 一覧に両方現れる
    res = client.get("/")
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO in res.text

    # 再走査すると「取り込み済み」になる
    res = client.get("/scan", params={"dir": str(root)})
    assert "取り込み済み" in res.text
    assert "未取り込み" not in res.text


def test_bulk_ingest_reports_bad_path(client, tmp_path):
    res = client.post(
        "/ingest/bulk", data={"paths": [str(tmp_path / "no_such_thing.zip")]}
    )
    assert res.status_code == 200
    assert "失敗" in res.text


def test_scan_missing_dir_shows_error(client, tmp_path):
    res = client.get("/scan", params={"dir": str(tmp_path / "nope")})
    assert res.status_code == 200
    assert "ディレクトリが存在しません" in res.text


def test_scan_excludes_archive_itself(client, config, tmp_path, totatuno_zip):
    """書庫自身の中の文書セットを再取り込み候補にしない。"""
    with open(totatuno_zip, "rb") as f:
        client.post(
            "/ingest", files=[("files", (totatuno_zip.name, f, "application/zip"))]
        )
    assert (config.docs_dir / TOTATUNO_TNO).is_dir()

    # 書庫を含む親ディレクトリを走査しても、書庫配下は候補に出ない
    res = client.get("/scan", params={"dir": str(config.archive.parent)})
    assert res.status_code == 200
    assert str(config.docs_dir) not in res.text
