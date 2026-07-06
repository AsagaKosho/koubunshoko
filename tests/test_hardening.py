"""堅牢化まわりの回帰テスト。

- iframe srcdoc へのCSP注入（外部送信ゼロ・NFR-03: 受動的な外部リソースの
  読み込みもブロックする）
- DBに登録済みの鑑XMLが書庫から欠落しても詳細ページを500にしない
- サーバー稼働中に catalog.db が削除されても一覧（再インデックス導線）を
  500にしない（受け入れ基準7の運用導線）
"""

from __future__ import annotations

from urllib.parse import quote

from conftest import TOTATUNO_TNO

KAGAMI_XML = f"{TOTATUNO_TNO}.xml"


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


# --- CSP（外部送信ゼロ） -------------------------------------------------------


def test_build_srcdoc_injects_csp_meta_first():
    from kobunshoko.main import build_srcdoc

    doc = build_srcdoc("<p>x</p>", base_href="/doc/1/view/")
    # CSPメタは文書の先頭（base・CSSより前）で宣言される
    assert doc.startswith('<meta http-equiv="Content-Security-Policy"')
    assert "default-src 'none'" in doc
    assert "img-src 'self' data:" in doc
    assert "style-src 'unsafe-inline'" in doc
    # 既存の注入（base href・CSS正規化）は維持される
    assert '<base href="/doc/1/view/"' in doc
    assert "<style>" in doc


def test_build_srcdoc_normalizes_pre_wrapping():
    # e-Gov様式はIEの「pre + word-wrap で折り返す」独自挙動に依存している。
    # 正規化CSSで pre-wrap を注入し、現代ブラウザでの列またぎの重なりを防ぐ
    from kobunshoko.main import build_srcdoc

    doc = build_srcdoc("<pre>長文</pre>", base_href="/doc/1/view/")
    assert "pre{white-space:pre-wrap;overflow-wrap:break-word;}" in doc


def test_detail_and_view_pages_embed_csp(client, totatuno_zip):
    _upload(client, totatuno_zip)

    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert res.status_code == 200
    assert "Content-Security-Policy" in res.text

    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote(KAGAMI_XML)}")
    assert res.status_code == 200
    assert "Content-Security-Policy" in res.text


# --- 書庫から鑑XMLが欠落しても詳細ページは500にしない ---------------------------


def test_doc_detail_survives_missing_kagami_file(client, config, totatuno_zip):
    _upload(client, totatuno_zip)
    # DBには登録済みのまま、書庫上の鑑XMLだけが失われた状態を作る
    (config.docs_dir / TOTATUNO_TNO / KAGAMI_XML).unlink()

    res = client.get(f"/doc/{TOTATUNO_TNO}")
    assert res.status_code == 200
    assert "簡易表示" in res.text  # フォールバックの明示
    # メタデータ・ファイル一覧など詳細ページ自体は閲覧できる
    assert TOTATUNO_TNO in res.text
    assert "01_控え文書.pdf" in res.text


def test_transform_xml_missing_file_returns_fallback_reason(tmp_path):
    from kobunshoko.render import transform_xml, xml_tree_html

    missing = tmp_path / "gone.xml"
    html_text, reason = transform_xml(missing, tmp_path)
    assert html_text is None
    assert reason  # 理由つきでフォールバック（例外を伝播させない）
    # ツリー表示側も例外を出さずメッセージHTMLを返す
    assert "<p>" in xml_tree_html(missing)


# --- 稼働中の catalog.db 削除からの復旧導線 -------------------------------------


def test_index_recovers_after_db_deleted_while_running(client, config, totatuno_zip):
    _upload(client, totatuno_zip)

    # サーバー稼働中に catalog.db を削除（受け入れ基準7の前提操作）
    config.db_path.unlink()

    # 一覧は500にならず、再インデックスボタンのある画面へ到達できる
    res = client.get("/")
    assert res.status_code == 200
    assert "再インデックス" in res.text

    # UI導線どおり POST /reindex で復元できる
    res = client.post("/reindex")
    assert res.status_code == 200
    assert "再構築しました" in res.text

    res = client.get("/")
    assert res.status_code == 200
    assert TOTATUNO_TNO in res.text
