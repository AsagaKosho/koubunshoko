"""タグ・メモ（FR-09）のテスト。

- 別テーブル保存（原本ファイルには一切書き込まない）
- 詳細画面での追加・削除・表示
- 一覧のタグ絞り込み
- 再インデックス（カタログ再構築）をまたいで注釈が保持されること
"""

from __future__ import annotations

import re

from conftest import DOCNO_TNO, TOTATUNO_TNO


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


def _snapshot_originals(config, tno):
    doc_dir = config.docs_dir / tno
    return sorted(
        (p.relative_to(doc_dir).as_posix(), p.read_bytes())
        for p in doc_dir.rglob("*")
        if p.is_file()
    )


def test_tag_add_show_delete(client, config, totatuno_zip):
    _upload(client, totatuno_zip)
    before = _snapshot_originals(config, TOTATUNO_TNO)

    # 追加 → 303 → 詳細に表示
    res = client.post(
        f"/doc/{TOTATUNO_TNO}/tags", data={"tag": "労基署"}, follow_redirects=False
    )
    assert res.status_code == 303
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "労基署" in res.text

    # 同じタグの二重追加は増えない
    client.post(f"/doc/{TOTATUNO_TNO}/tags", data={"tag": "労基署"})
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    assert db_mod.list_tags(conn, TOTATUNO_TNO) == ["労基署"]
    conn.close()

    # 削除 → 詳細から消える
    res = client.post(
        f"/doc/{TOTATUNO_TNO}/tags/delete",
        data={"tag": "労基署"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "労基署" not in res.text

    # 原本ファイルには一切書き込まれていない
    assert _snapshot_originals(config, TOTATUNO_TNO) == before


def test_empty_tag_is_ignored(client, config, totatuno_zip):
    _upload(client, totatuno_zip)
    client.post(f"/doc/{TOTATUNO_TNO}/tags", data={"tag": "   "})
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    assert db_mod.list_tags(conn, TOTATUNO_TNO) == []
    conn.close()


def test_tag_routes_404_for_unknown_doc(client):
    assert client.post("/doc/999/tags", data={"tag": "x"}).status_code == 404
    assert client.post("/doc/999/tags/delete", data={"tag": "x"}).status_code == 404
    assert client.post("/doc/999/memos", data={"body": "x"}).status_code == 404
    assert client.post("/doc/../x/tags", data={"tag": "x"}).status_code == 404


def test_index_filter_by_tag(client, totatuno_zip, docno_zip):
    _upload(client, totatuno_zip)
    _upload(client, docno_zip)
    client.post(f"/doc/{DOCNO_TNO}/tags", data={"tag": "年金"})

    # タグ絞り込み: 付いている文書だけが残る
    res = client.get("/", params={"tag": "年金"})
    assert DOCNO_TNO in res.text
    assert TOTATUNO_TNO not in res.text

    # 絞り込みフォームの選択肢と一覧行のタグバッジ
    res = client.get("/")
    assert res.text.count("年金") >= 2  # select option + 行内バッジ
    assert TOTATUNO_TNO in res.text and DOCNO_TNO in res.text

    # 存在しないタグでは0件
    res = client.get("/", params={"tag": "そんなタグはない"})
    assert DOCNO_TNO not in res.text
    assert TOTATUNO_TNO not in res.text


def test_memo_add_show_delete(client, config, totatuno_zip):
    _upload(client, totatuno_zip)

    res = client.post(
        f"/doc/{TOTATUNO_TNO}/memos",
        data={"body": "36協定の控え。\n更新期限に注意"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "36協定の控え。" in res.text
    assert "更新期限に注意" in res.text

    # 空メモは登録されない
    client.post(f"/doc/{TOTATUNO_TNO}/memos", data={"body": "  \n "})
    from kobunshoko import db as db_mod

    conn = db_mod.connect(config.db_path)
    memos = db_mod.list_memos(conn, TOTATUNO_TNO)
    conn.close()
    assert len(memos) == 1
    assert memos[0]["created_at"]  # 作成日時が付く

    # 削除フォームのURLから memo_id を拾って削除
    m = re.search(rf"/doc/{TOTATUNO_TNO}/memos/(\d+)/delete", res.text)
    assert m
    res = client.post(
        f"/doc/{TOTATUNO_TNO}/memos/{m.group(1)}/delete", follow_redirects=False
    )
    assert res.status_code == 303
    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "36協定の控え。" not in res.text


def test_annotations_survive_reindex(client, config, totatuno_zip):
    """タグ・メモはファイルシステムから導出できないため、
    カタログ再構築（NFR-02）で消えてはならない。"""
    _upload(client, totatuno_zip)
    client.post(f"/doc/{TOTATUNO_TNO}/tags", data={"tag": "保存版"})
    client.post(f"/doc/{TOTATUNO_TNO}/memos", data={"body": "再構築テスト用メモ"})

    res = client.post("/reindex")
    assert "再構築しました" in res.text

    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert "保存版" in res.text
    assert "再構築テスト用メモ" in res.text

    # 一覧のタグ絞り込みも生きている
    res = client.get("/", params={"tag": "保存版"})
    assert TOTATUNO_TNO in res.text
