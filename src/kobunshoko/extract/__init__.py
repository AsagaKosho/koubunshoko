"""鑑XMLメタデータ抽出のパターンレジストリ。

先勝ちで判定する。新しい省庁の形式に対応するときは、パターンクラスを追加して
PATTERNS に1行足すだけでよい。全パターンに合致しない場合は呼び出し側が
フォールバック（pattern='fallback'、形式未対応扱い）で登録する。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lxml import etree

from .base import FALLBACK_PATTERN_NAME, KagamiPattern, Meta, wareki_to_iso
from .docno import DocnoPattern
from .totatuno import TotatunoPattern

__all__ = [
    "PATTERNS",
    "FALLBACK_PATTERN_NAME",
    "KagamiPattern",
    "Meta",
    "wareki_to_iso",
    "match_pattern",
    "fallback_meta",
]

PATTERNS: list[KagamiPattern] = [TotatunoPattern(), DocnoPattern()]

_TNO_DIGITS_RE = re.compile(r"\d{14,20}")


def match_pattern(root: etree._Element) -> KagamiPattern | None:
    for pattern in PATTERNS:
        if pattern.matches(root):
            return pattern
    return None


def fallback_meta(source_name: str, source_path: Path | None = None) -> Meta:
    """全パターン不一致時のフォールバック値（FR-02）。

    到達番号=名前中の数字列（14〜20桁）、日付=ファイル更新日時、件名=ZIP/フォルダ名。
    """
    m = _TNO_DIGITS_RE.search(source_name)
    received_date = None
    if source_path is not None and source_path.exists():
        received_date = (
            datetime.fromtimestamp(source_path.stat().st_mtime).date().isoformat()
        )
    return Meta(
        tno=m.group(0) if m else None,
        received_date=received_date,
        title=source_name,
    )
