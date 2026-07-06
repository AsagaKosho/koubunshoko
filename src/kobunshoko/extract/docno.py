"""年金機構 kagami 系（DOCNO）の鑑XML抽出パターン。

構造の例:
  DOC/BODY/DOCNO       … 到達番号（文書番号）
  DOC/BODY/DATE        … 発行日（和暦）
  DOC/BODY/AUTHOR/AFF, AUTHOR/NAME … 機関
  DOC/BODY/TITLE       … 件名
  DOC/BODY/APPENDIX*/DOCLINK       … 添付（様式XML・CSV明細等）
"""

from __future__ import annotations

from lxml import etree

from .base import Meta, text_of


class DocnoPattern:
    name = "docno"

    def matches(self, root: etree._Element) -> bool:
        return root.find("BODY/DOCNO") is not None

    def extract(self, root: etree._Element) -> Meta:
        body = root.find("BODY")
        doclinks: list[str] = []
        if body is not None:
            for child in body:
                if not isinstance(child.tag, str):
                    continue
                if not etree.QName(child).localname.startswith("APPENDIX"):
                    continue
                for dl in child.findall("DOCLINK"):
                    uri = (dl.text or "").strip() or dl.get("URI")
                    if uri:
                        doclinks.append(uri)
        author = body.find("AUTHOR") if body is not None else None
        return Meta(
            tno=text_of(body, "DOCNO"),
            received_raw=text_of(body, "DATE"),
            agency=text_of(author, "AFF") or text_of(author, "NAME"),
            title=text_of(body, "TITLE"),
            doclinks=doclinks,
        )
