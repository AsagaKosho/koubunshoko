"""ZIPエントリ名の cp437→cp932 修復（設計 6-1）の単体テスト。"""

import zipfile

from kobunshoko.ingest import extract_zip, repair_zip_name
from zipbuild import write_zip


def _mangle(name: str) -> str:
    """zipfile がUTF-8フラグなしエントリを読んだときの見え方を再現する。"""
    return name.encode("cp932").decode("cp437")


def test_repair_mangled_name():
    original = "01_控え文書.pdf"
    assert repair_zip_name(_mangle(original), flag_bits=0) == original


def test_repair_keeps_utf8_flagged_name():
    # UTF-8フラグがあれば復号済みなので手を付けない
    assert repair_zip_name("01_控え文書.pdf", flag_bits=0x800) == "01_控え文書.pdf"


def test_repair_ascii_name_unchanged():
    assert repair_zip_name("original.zip", flag_bits=0) == "original.zip"


def test_repair_unrepairable_name_kept():
    # cp437にエンコードできない名前（既にUnicodeの日本語）はそのまま受け入れる
    assert repair_zip_name("控え.pdf", flag_bits=0) == "控え.pdf"


def test_extract_zip_repairs_names(tmp_path):
    zp = tmp_path / "t.zip"
    write_zip(
        zp,
        [("209906019912345678/鑑文書.xml", b"<a/>"), ("209906019912345678/b.pdf", b"x")],
        encoding="cp932",
        utf8_flag=False,
    )
    # zipfile 素読みでは名前が化けていることを前提確認
    with zipfile.ZipFile(zp) as zf:
        assert any("鑑" not in n for n in zf.namelist())
    dest = tmp_path / "out"
    dest.mkdir()
    extract_zip(zp, dest)
    assert (dest / "209906019912345678" / "鑑文書.xml").read_bytes() == b"<a/>"
    assert (dest / "209906019912345678" / "b.pdf").is_file()


def test_extract_zip_blocks_zip_slip(tmp_path):
    import pytest

    from kobunshoko.ingest import IngestError

    zp = tmp_path / "evil.zip"
    write_zip(zp, [("../evil.txt", b"pwned")], encoding="ascii", utf8_flag=False)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(IngestError):
        extract_zip(zp, dest)
    assert not (tmp_path / "evil.txt").exists()
