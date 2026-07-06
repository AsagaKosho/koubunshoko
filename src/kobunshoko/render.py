"""閲覧用変換（サーバー側XSLT・CSVテーブル・汎用XMLツリー表示）。

原本には一切書き込まない。変換はすべてオンザフライで行い、
ブラウザへは常にUTF-8で配信する。
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from html import escape
from pathlib import Path
from urllib.parse import unquote

from charset_normalizer import from_bytes
from lxml import etree

from .ingest import make_xml_parser

logger = logging.getLogger(__name__)

# 外部リソース取得ゼロ（設計 §9-2）。read_file は xsl:include / document() のために許可
# （lxmlのアクセス制御にディレクトリ単位の指定はないため、緩和はここまでに留める）
XSLT_ACCESS = etree.XSLTAccessControl(
    read_file=True, write_file=False, create_dir=False,
    read_network=False, write_network=False,
)

# XSLの shift_jis 出力指定などで埋め込まれる charset をUTF-8に書き換える
_CHARSET_RE = re.compile(r"(charset\s*=\s*[\"']?)([A-Za-z0-9_\-]+)", re.IGNORECASE)


def find_stylesheet(tree: etree._ElementTree, xml_path: Path, doc_dir: Path) -> Path | None:
    """<?xml-stylesheet?> の href を同一文書セット内のXSLに解決する。

    href はパーセントエンコード済みの場合があるためデコードし、
    文書セットディレクトリの外を指すものは拒否する。
    """
    href = None
    node = tree.getroot().getprevious()
    while node is not None:
        if (
            isinstance(node, etree._ProcessingInstruction)
            and node.target == "xml-stylesheet"
        ):
            href = node.get("href")
            if href:
                break
        node = node.getprevious()
    if not href:
        return None
    name = unicodedata.normalize("NFC", unquote(href))
    if "://" in name or name.startswith("/"):
        return None
    doc_dir_resolved = doc_dir.resolve()
    candidate = (xml_path.parent / name).resolve()
    if not candidate.is_relative_to(doc_dir_resolved):
        return None
    if not candidate.is_file():
        # macOSのNFD正規化との差異を吸収
        alt = (xml_path.parent / unicodedata.normalize("NFD", name)).resolve()
        if alt.is_relative_to(doc_dir_resolved) and alt.is_file():
            return alt
        return None
    return candidate


def transform_xml(xml_path: Path, doc_dir: Path) -> tuple[str | None, str | None]:
    """XMLを対のXSLでHTMLに変換する。

    戻り値は (html, 失敗理由)。html が None のときは呼び出し側が
    汎用XMLツリー表示へフォールバックする。
    """
    parser = make_xml_parser()
    try:
        tree = etree.parse(str(xml_path), parser)
    except etree.XMLSyntaxError as e:
        return None, f"XMLの解析に失敗しました: {e}"
    except OSError as e:
        # DBに登録済みでも書庫から欠落していることがある（真実の源はファイルシステム）。
        # 詳細ページ全体を500にせず、フォールバック表示に理由を出す
        return None, f"XMLを読み取れませんでした: {e}"

    xsl_path = find_stylesheet(tree, xml_path, doc_dir)
    if xsl_path is None:
        return None, "対のXSLスタイルシートが見つかりません"

    try:
        xslt = etree.XSLT(
            etree.parse(str(xsl_path), parser), access_control=XSLT_ACCESS
        )
        result = xslt(tree)
    except (
        etree.XSLTParseError,
        etree.XSLTApplyError,
        etree.XMLSyntaxError,
        OSError,
    ) as e:
        return None, f"XSLT変換に失敗しました: {e}"

    html_text = str(result)
    if not html_text.strip():
        return None, "XSLT変換の結果が空でした"
    # shift_jis 等の出力指定に関わらずUTF-8で配信する（FR-04）
    html_text = _CHARSET_RE.sub(lambda m: m.group(1) + "UTF-8", html_text)
    return html_text, None


# --- 汎用XMLツリー表示（XSL欠損・変換失敗時のフォールバック） -----------------


def xml_tree_html(xml_path: Path) -> str:
    """要素名と値をネストした <dl> で整形する。Signature要素は折りたたむ。"""
    try:
        root = etree.parse(str(xml_path), make_xml_parser()).getroot()
    except (etree.XMLSyntaxError, OSError) as e:
        # 書庫からの欠落（OSError）でも500にせず、ページ内にメッセージを出す
        return f"<p>XMLを解析できませんでした: {escape(str(e))}</p>"
    return f'<div class="xmltree"><dl>{_element_html(root)}</dl></div>'


def _element_html(el: etree._Element) -> str:
    name = escape(etree.QName(el).localname)
    attrs = "".join(
        f' <span class="attr">{escape(str(k))}="{escape(str(v))}"</span>'
        for k, v in el.attrib.items()
    )
    children = [c for c in el if isinstance(c.tag, str)]
    if etree.QName(el).localname == "Signature":
        inner = "".join(_element_html(c) for c in children)
        return (
            f"<dt><details><summary>{name}（電子署名）</summary>"
            f"<dl>{inner}</dl></details></dt>"
        )
    if children:
        inner = "".join(_element_html(c) for c in children)
        return f"<dt>{name}{attrs}</dt><dd><dl>{inner}</dl></dd>"
    text = (el.text or "").strip()
    value = escape(text) if text else '<span class="empty">（空）</span>'
    return f"<dt>{name}{attrs}</dt><dd>{value}</dd>"


# --- CSV表示 -----------------------------------------------------------------


def decode_csv_bytes(data: bytes) -> str | None:
    """CSVバイト列の文字コードを判定して文字列化する（cp932を優先候補に）。"""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # BOM付きなのに本体がUTF-8でない壊れたCSV。閲覧を500にしないため
            # BOMを除いた本体を通常の判定（cp932優先）に回す
            data = data[len(b"\xef\xbb\xbf"):]
    best = from_bytes(data, cp_isolation=["cp932", "utf_8"]).best()
    if best is not None:
        return str(best)
    for enc in ("cp932", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def read_dta_preview(path: Path) -> tuple[list[str], int]:
    """DTAファイルの先頭にあるcp932テキストレコードを読めるところまで返す。

    日本年金機構の被保険者データ（SHFD0039.DTA）等は、事業所情報などの
    cp932テキストレコード（CRLF区切り）の後に、届書作成プログラムでのみ
    読み込める暗号化データが続く。戻り値は (読めた行, 残りのバイト数)。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return [], 0
    lines: list[str] = []
    offset = 0
    while offset < len(data):
        end = data.find(b"\r\n", offset)
        if end < 0:
            break
        try:
            text = data[offset:end].decode("cp932")
        except UnicodeDecodeError:
            break
        # 制御文字混じり＝暗号化部の始まり（全角スペース等の区切り文字は許容）
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
            break
        lines.append(text)
        offset = end + 2
    return lines, len(data) - offset


def read_csv_rows(path: Path) -> list[list[str]] | None:
    """CSVを行列に読む。文字コード判定に失敗したら None（ダウンロード案内へ）。

    セル内の <br/> や <a href> 断片は文字列のまま返し、テンプレート側の
    エスケープでそのまま文字として表示する（HTMLとして解釈しない）。
    """
    text = decode_csv_bytes(path.read_bytes())
    if text is None:
        return None
    try:
        return list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        logger.warning("CSVのパースに失敗: %s (%s)", path, e)
        return None
