"""CSV文字コード判定（設計 7-2）の単体テスト。"""

import shutil

from conftest import DOCNO_TNO, FIXTURES
from kobunshoko.render import decode_csv_bytes, read_csv_rows


def test_decode_cp932():
    data = '"機構からのお知らせ","99ﾃｽﾄ","株式会社　テスト商事"'.encode("cp932")
    text = decode_csv_bytes(data)
    assert text is not None
    assert "機構からのお知らせ" in text
    assert "99ﾃｽﾄ" in text  # 半角カナも化けない


def test_decode_utf8():
    data = "件名,発行機関\n審査結果,労働基準監督署\n".encode("utf-8")
    text = decode_csv_bytes(data)
    assert text is not None
    assert "労働基準監督署" in text


def test_decode_utf8_bom():
    data = b"\xef\xbb\xbf" + "件名,機関\n".encode("utf-8")
    assert decode_csv_bytes(data) == "件名,機関\n"


def test_decode_utf8_bom_with_cp932_body_does_not_raise():
    """BOM付きだが本体がUTF-8でない壊れたCSVでも例外を出さない（閲覧500防止）。
    BOMを除いた本体は通常の判定（cp932優先）で救済される。"""
    data = b"\xef\xbb\xbf" + '列1,列2\n"株式会社　テスト商事",通知\n'.encode("cp932")
    text = decode_csv_bytes(data)
    assert text is not None
    assert "列1" in text
    assert "株式会社　テスト商事" in text


def test_view_csv_with_bom_and_cp932_body_returns_200(client, config, tmp_path):
    """BOM+cp932本体のCSVを /doc/{tno}/view/ で開いても500にならない。"""
    src = tmp_path / DOCNO_TNO
    shutil.copytree(FIXTURES / "docno_set", src)
    (src / "bom_broken.csv").write_bytes(
        b"\xef\xbb\xbf" + "列1,列2\n通知,詳細\n".encode("cp932")
    )
    res = client.post("/ingest/path", data={"path": str(src)})
    assert "成功" in res.text

    res = client.get(f"/doc/{DOCNO_TNO}/view/bom_broken.csv")
    assert res.status_code == 200
    # テーブル表示（判定成功）またはダウンロード案内のいずれかに落ちる
    assert ("列1" in res.text) or ("ダウンロード" in res.text)


def test_fixture_csv_rows_and_escaping_source():
    path = next((FIXTURES / "docno_set").glob("*.csv"))
    rows = read_csv_rows(path)
    assert rows is not None
    assert rows[0][0] == "機構からのお知らせ"
    # セル内の <br/> / <a href> 断片は文字列のまま保持される（HTML解釈しない）
    body = rows[1][0]
    assert "<br/>" in body and '<a href="https://example.invalid/">' in body
    assert rows[1][1] == "99ﾃｽﾄ"
    assert DOCNO_TNO not in rows[0][0]  # ヘッダは案内文言
