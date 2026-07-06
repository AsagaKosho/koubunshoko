"""DTAファイル（届書作成プログラム形式）のプレビュー。

被保険者データ（SHFD0039.DTA）は cp932 テキストレコード（事業所情報など）の
後に暗号化された被保険者レコードが続く。読める部分だけをプレビューし、
暗号化部分には届書作成プログラムでの開き方を案内する。
"""

from __future__ import annotations

import shutil
from urllib.parse import quote

from conftest import DOCNO_TNO, FIXTURES


def _synthetic_dta() -> bytes:
    # 実物と同じ構造: cp932テキスト2レコード（CRLF区切り）＋バイナリ部
    line1 = "34ﾃｽﾄ 00120260619     01".encode("cp932")
    line2 = (
        "34,ﾃｽﾄ ,99999,305,0033,つくば市　どこか１－２－３,"
        "株式会社　テスト商事,架空　太郎,0000000000 ,    "
    ).encode("cp932")
    binary = bytes([0x05, 0xE5, 0x58, 0x9F, 0x36, 0x28, 0x16, 0xDC]) * 40
    return line1 + b"\r\n" + line2 + b"\r\n" + binary


def test_read_dta_preview_splits_text_and_encrypted(tmp_path):
    from kobunshoko.render import read_dta_preview

    p = tmp_path / "SHFD0039.DTA"
    p.write_bytes(_synthetic_dta())
    lines, encrypted = read_dta_preview(p)
    assert len(lines) == 2
    assert "34ﾃｽﾄ" in lines[0]
    assert "株式会社　テスト商事" in lines[1]
    assert encrypted == 320


def test_read_dta_preview_all_binary(tmp_path):
    from kobunshoko.render import read_dta_preview

    p = tmp_path / "opaque.dta"
    p.write_bytes(bytes(range(256)))
    lines, encrypted = read_dta_preview(p)
    assert lines == []
    assert encrypted == 256


def test_dta_view_and_role(client, config, tmp_path):
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    (src / "SHFD0039.DTA").write_bytes(_synthetic_dta())

    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text

    # 役割は dta として登録され、詳細画面からviewリンクが張られる
    res = client.get(f"/doc/{DOCNO_TNO}")
    assert ">dta<" in res.text
    assert f"/doc/{DOCNO_TNO}/view/SHFD0039.DTA" in res.text

    # プレビュー: 読めるレコードの表示＋暗号化部分の案内
    res = client.get(f"/doc/{DOCNO_TNO}/view/{quote('SHFD0039.DTA')}")
    assert res.status_code == 200
    assert "株式会社　テスト商事" in res.text
    assert "320" in res.text  # 暗号化部分のバイト数
    assert "届書作成プログラム" in res.text
    assert "読み込みパスワード" in res.text


def test_dta_view_all_binary_shows_guidance_only(client, config, tmp_path):
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    (src / "SHFD0039.DTA").write_bytes(bytes([0x9F, 0x05]) * 100)

    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text
    res = client.get(f"/doc/{DOCNO_TNO}/view/SHFD0039.DTA")
    assert res.status_code == 200
    # 読めるレコードなし → プレビュー表なしで、開き方の案内のみ
    assert "読み取り可能なレコード" not in res.text
    assert "届書作成プログラム" in res.text
