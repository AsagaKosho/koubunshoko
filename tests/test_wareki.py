"""和暦→西暦変換（設計 6-4）の単体テスト。"""

import pytest

from kobunshoko.extract import wareki_to_iso


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("令和 8年 6月27日", "2026-06-27"),
        ("令和8年1月22日", "2026-01-22"),
        ("令和８年１月２２日", "2026-01-22"),  # 全角数字
        ("令和　8年　6月　1日", "2026-06-01"),  # 全角空白
        ("令和元年5月1日", "2019-05-01"),
        ("平成31年4月30日", "2019-04-30"),
        ("平成元年1月8日", "1989-01-08"),
        ("昭和64年1月7日", "1989-01-07"),
        ("発行日：令和 8年 6月27日（金）", "2026-06-27"),  # 前後に文字があっても拾う
    ],
)
def test_wareki_to_iso(text, expected):
    assert wareki_to_iso(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "2026-06-27",  # 既に西暦（和暦ではない）
        "令和8年",  # 月日なし
        "大正15年1月1日",  # 未対応元号
        "令和8年13月1日",  # 不正な月
        "令和8年2月30日",  # 不正な日
    ],
)
def test_wareki_to_iso_unconvertible(text):
    assert wareki_to_iso(text) is None
