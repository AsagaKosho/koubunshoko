"""印刷用ページ（ブラウザの「PDFとして保存」向け）。

変換HTMLをiframeなしの単独ページで配信する。スクリプト遮断は
sandboxの代わりにCSPヘッダで担保し、案内バーは印刷時に消える。
"""

from __future__ import annotations

import shutil
from urllib.parse import quote

from conftest import FIXTURES, TOTATUNO_TNO


def _ingest(client, tmp_path):
    src = tmp_path / TOTATUNO_TNO
    shutil.copytree(FIXTURES / "totatuno_set", src)
    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text


def test_print_page_serves_transformed_html_with_csp(client, tmp_path):
    _ingest(client, tmp_path)
    res = client.get(f"/doc/{TOTATUNO_TNO}/print/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    # 変換結果が単独ページで返る（iframeなし）
    assert "電子申請に対する審査結果について" in res.text
    assert "<iframe" not in res.text
    # スクリプト遮断はCSPヘッダで担保（外部送信ゼロも維持）
    assert "default-src 'none'" in res.headers["content-security-policy"]
    # 案内バーは印刷時に非表示
    assert "print-hint" in res.text
    assert "@media print{.print-hint{display:none;}}" in res.text
    # pre折り返し正規化も適用される
    assert "pre{white-space:pre-wrap" in res.text


def test_print_page_redirects_non_xml(client, tmp_path):
    _ingest(client, tmp_path)
    res = client.get(
        f"/doc/{TOTATUNO_TNO}/print/{quote('01_控え文書.pdf')}",
        follow_redirects=False,
    )
    assert res.status_code in (302, 307)
    assert "/view/" in res.headers["location"]


def test_print_page_falls_back_without_xsl(client, tmp_path):
    src = tmp_path / TOTATUNO_TNO
    shutil.copytree(FIXTURES / "totatuno_set", src)
    (src / "鑑文書表示用スタイルシート.xsl").unlink()
    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text

    res = client.get(f"/doc/{TOTATUNO_TNO}/print/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert res.status_code == 200
    assert "TOTATUNO" in res.text  # 汎用ツリー表示でも印刷できる


def test_view_page_links_to_print(client, tmp_path):
    _ingest(client, tmp_path)
    res = client.get(f"/doc/{TOTATUNO_TNO}/view/{quote(f'{TOTATUNO_TNO}.xml')}")
    assert f"/doc/{TOTATUNO_TNO}/print/" in res.text
    assert "印刷 / PDF保存" in res.text
