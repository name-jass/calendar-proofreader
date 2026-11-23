"""历法真值：一个月应有的天数、1 号是星期几、每一格该放什么日期。"""
from __future__ import annotations

import calendar
import datetime

# Python 的 weekday(): 周一=0 ... 周日=6
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
WEEKDAY_FULL = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

FIRST_COL = {"mon": 0, "sun": 6}


def month_truth(year: int, month: int, first_col: str = "mon") -> dict:
    """返回该月的真值信息。"""
    days = calendar.monthrange(year, month)[1]
    first_wd = datetime.date(year, month, 1).weekday()
    offset = (first_wd - FIRST_COL[first_col]) % 7
    return {
        "year": year,
        "month": month,
        "days": days,
        "first_wd": first_wd,
        "offset": offset,
        "weeks": (offset + days + 6) // 7,
        "first_col": first_col,
    }


def expected_cell(value: int, truth: dict) -> tuple[int, int]:
    """某个日期理论上应落在 (行, 列)。行从该月第一行算起。"""
    idx = truth["offset"] + value - 1
    return idx // 7, idx % 7


def weekday_name(value: int, truth: dict) -> str:
    """某个日期是星期几。"""
    wd = (truth["first_wd"] + value - 1) % 7
    return WEEKDAY_FULL[wd]
