"""厚労省審査結果系（TOTATUNO）の鑑XML抽出パターン。

構造の例:
  DOC/BODY/TOTATUNO  … 到達番号
  DOC/BODY/UKEDATE   … 受付日（和暦）
  DOC/BODY/ATESK, SYOZOKUSOSIKI … 機関
  DOC/APPENDIX/DOCLINK@URI      … 添付（控えPDF等）
"""

from __future__ import annotations

from lxml import etree

from .base import Meta, text_of

# 件名は鑑XMLに存在しないため、XSL上の見出しに一致する固定文言を使う
FIXED_TITLE = "電子申請に対する審査結果について"


class TotatunoPattern:
    name = "totatuno"

    def matches(self, root: etree._Element) -> bool:
        return root.find("BODY/TOTATUNO") is not None

    def extract(self, root: etree._Element) -> Meta:
        body = root.find("BODY")
        doclinks: list[str] = []
        for appendix in root.iter():
            if not isinstance(appendix.tag, str):
                continue
            if not etree.QName(appendix).localname.startswith("APPENDIX"):
                continue
            for dl in appendix.findall("DOCLINK"):
                uri = dl.get("URI") or (dl.text or "").strip()
                if uri:
                    doclinks.append(uri)
        return Meta(
            tno=text_of(body, "TOTATUNO"),
            received_raw=text_of(body, "UKEDATE"),
            agency=text_of(body, "ATESK") or text_of(body, "SYOZOKUSOSIKI"),
            title=FIXED_TITLE,
            doclinks=doclinks,
        )
