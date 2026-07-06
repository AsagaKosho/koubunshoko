"""抽出パターンの共通定義（Meta・Protocol・和暦変換ユーティリティ）。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from lxml import etree

FALLBACK_PATTERN_NAME = "fallback"


@dataclass
class Meta:
    """鑑XMLから抽出したメタデータ。"""

    tno: str | None = None
    received_raw: str | None = None  # 和暦などの日付原文
    received_date: str | None = None  # ISO 8601（フォールバック時のみ抽出側が設定）
    agency: str | None = None
    title: str | None = None
    doclinks: list[str] = field(default_factory=list)  # 鑑から参照される添付ファイル名


@runtime_checkable
class KagamiPattern(Protocol):
    """鑑XMLの形式判定＋抽出。PATTERNS に追加するだけで拡張できる。"""

    name: str

    def matches(self, root: etree._Element) -> bool: ...

    def extract(self, root: etree._Element) -> Meta: ...


def text_of(parent: etree._Element | None, path: str) -> str | None:
    """子要素のテキストを取得（空白除去。空文字はNoneに落とす）。"""
    if parent is None:
        return None
    el = parent.find(path)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


# --- 和暦→西暦変換 -----------------------------------------------------------

# 元号の開始年 - 1（元号年を足すと西暦になる値）
_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}

# 「令和 8年 6月27日」「令和８年１月２２日」「平成元年5月1日」等の揺れを吸収する。
# \s は全角空白（U+3000）にもマッチする。
_WAREKI_RE = re.compile(
    r"(令和|平成|昭和)\s*(元|[0-9０-９]+)\s*年\s*([0-9０-９]+)\s*月\s*([0-9０-９]+)\s*日"
)


def _to_int(s: str) -> int:
    if s == "元":
        return 1
    return int(unicodedata.normalize("NFKC", s))


def wareki_to_iso(text: str | None) -> str | None:
    """和暦日付文字列を ISO 8601 (YYYY-MM-DD) に変換する。変換不能なら None。"""
    if not text:
        return None
    m = _WAREKI_RE.search(text)
    if m is None:
        return None
    era, y, mo, d = m.groups()
    try:
        year = _ERA_BASE[era] + _to_int(y)
        parsed = date(year, _to_int(mo), _to_int(d))
    except ValueError:
        return None
    return parsed.isoformat()
