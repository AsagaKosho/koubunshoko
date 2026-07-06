"""鑑XMLパターン抽出（FR-02）の単体テスト。"""

from pathlib import Path

from lxml import etree

from conftest import DOCNO_TNO, FIXTURES, TOTATUNO_TNO
from kobunshoko.extract import fallback_meta, match_pattern
from kobunshoko.ingest import find_kagami, make_xml_parser


def _root(path: Path):
    return etree.parse(str(path), make_xml_parser()).getroot()


def test_totatuno_pattern():
    root = _root(FIXTURES / "totatuno_set" / f"{TOTATUNO_TNO}.xml")
    pattern = match_pattern(root)
    assert pattern is not None and pattern.name == "totatuno"
    meta = pattern.extract(root)
    assert meta.tno == TOTATUNO_TNO
    assert meta.received_raw == "令和 8年 6月 1日"
    assert meta.agency == "架空労働基準監督署"
    assert meta.title == "電子申請に対する審査結果について"
    assert meta.doclinks == ["01_控え文書.pdf"]


def test_docno_pattern():
    root = _root(FIXTURES / "docno_set" / f"{DOCNO_TNO}.xml")
    pattern = match_pattern(root)
    assert pattern is not None and pattern.name == "docno"
    meta = pattern.extract(root)
    assert meta.tno == DOCNO_TNO
    assert meta.received_raw == "令和８年１月２２日"
    assert meta.agency == "架空年金機構"
    assert meta.title == "架空年金機構からのお知らせ（合成テストデータ）"
    # APPENDIX と APPENDIX2 の両方から添付を拾う
    assert meta.doclinks == [
        f"テスト通知書_令和8年1月送付分({DOCNO_TNO}).xml",
        f"テスト通知書_令和8年1月送付分_明細({DOCNO_TNO}).csv",
    ]


def test_unknown_schema_matches_no_pattern():
    root = etree.fromstring("<UNKNOWN><FOO>bar</FOO></UNKNOWN>")
    assert match_pattern(root) is None


def test_fallback_meta_extracts_digits_and_mtime(tmp_path):
    src = tmp_path / "20990101019999999_20260706.zip"
    src.write_bytes(b"dummy")
    meta = fallback_meta(src.name, src)
    assert meta.tno == "20990101019999999"
    assert meta.title == src.name
    assert meta.received_date is not None  # ファイル更新日時から


def test_fallback_meta_without_digits():
    meta = fallback_meta("notice.zip", None)
    assert meta.tno is None
    assert meta.received_date is None


def test_find_kagami_prefers_doc_with_signature():
    path, root = find_kagami(FIXTURES / "docno_set")
    # 様式XMLもルート直下にあるが、Signature付きDOCの鑑が選ばれる
    assert path is not None and path.name == f"{DOCNO_TNO}.xml"
    assert root.find("BODY/DOCNO") is not None


def test_find_kagami_by_kagami_name(tmp_path):
    (tmp_path / "kagami.xml").write_text(
        "<?xml version='1.0'?><NOTICE><NO>1</NO></NOTICE>", encoding="utf-8"
    )
    path, _ = find_kagami(tmp_path)
    assert path is not None and path.name == "kagami.xml"


def test_find_kagami_none(tmp_path):
    (tmp_path / "data.csv").write_text("a,b", encoding="utf-8")
    path, root = find_kagami(tmp_path)
    assert path is None and root is None
