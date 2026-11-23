"""校对规则：拿历法真值和页面读到的日期逐格对账。

这一层不再关心"字怎么认出来的"，只负责：
    1) 按文件自己的版式，逐格看是不是 1、2、3… 连续
    2) 按真实历法，看每一格有没有落在正确的星期列上
"""
from __future__ import annotations

from collections import defaultdict

from .layers import NEXT, PREV
from .truth import FIRST_COL, WEEKDAY_FULL, month_truth

MIN_CONF = 0.25       # 字形分组的置信度（第一名领先第二名）低于此值才提示人工看


def build_truth(year: int, month: int, first_col: str) -> dict:
    return month_truth(year, month, first_col)


def anchor_mismatch(cells: dict, truth: dict, r0: int, c0: int, anchor: str) -> int:
    """假设"第一列是星期 X"，数一数有多少个日期不在该在的星期列上。"""
    bad = 0
    first = FIRST_COL[anchor]
    for (r, c), info in cells.items():
        if info.get("expect") in (PREV, NEXT):
            continue
        v = info.get("value")
        if v is None or not (1 <= v <= truth["days"]):
            continue
        col_wd = (first + c) % 7
        true_wd = (truth["first_wd"] + v - 1) % 7
        if col_wd != true_wd:
            bad += 1
    return bad


def verify_page(page_no: int, month: int, cells: dict, truth: dict,
                r0: int, c0: int, anchor: str,
                layout: str = "grid", cols: int = 7) -> dict:
    days = truth["days"]
    findings: list[dict] = []

    def add(kind, sev, msg, cell=None, note=""):
        findings.append({"kind": kind, "severity": sev, "msg": msg,
                         "cell": cell, "note": note})

    def cell_of(v: int) -> tuple[int, int]:
        if layout == "linear":
            return ((v - 1) // max(cols, 1), (v - 1) % max(cols, 1))
        g = c0 + v - 1
        return (r0 + g // 7, g % 7)

    # ---------- 1. 按版式逐格对：漏排 / 错号 ----------
    covered: set[int] = set()
    wrong: dict[tuple[int, int], tuple[int, int]] = {}
    unreadable: list[tuple[int, tuple[int, int]]] = []
    for (r, c), info in cells.items():
        want = info.get("expect")
        got = info.get("value")
        if want in (PREV, NEXT):
            continue
        if want is not None:
            covered.add(want)
        if got is not None:
            covered.add(got)
        if want is None:
            continue
        if got is None:
            unreadable.append((want, (r, c)))
        elif got != want:
            wrong[(r, c)] = (want, got)

    missing = [v for v in range(1, days + 1) if v not in covered]
    prev_cells: dict[int, tuple[int, int]] = {}
    for (r, c), info in cells.items():
        if info.get("expect") in (PREV, NEXT):
            continue
        v = info.get("value")
        if v is not None and 1 <= v <= days:
            prev_cells.setdefault(v, (r, c))
    for v in missing:
        if v in prev_cells:
            add("放错位置", "error",
                f"{month} 月 {v} 日 印在了首行最左边（那里是上个月末尾的位置），"
                f"本月的格子里没有它",
                cell=prev_cells[v], note="这一格应该是上个月的日期")
        else:
            add("漏排", "error",
                f"{month} 月 {v} 日 这一格是空的，缺日期", cell=cell_of(v))

    for cell, (want, got) in wrong.items():
        add("错号", "error",
            f"{month} 月 {want:02d} 日 的位置印成了 {got:02d}",
            cell=cell, note=f"这一格应该是 {want:02d}")

    for want, cell in unreadable:
        add("待确认", "warn",
            f"{month} 月 {want} 日 这一格读不出日期（字形可能粘连，或这一格不是数字）",
            cell=cell, note="建议人工看一眼这一格")

    # ---------- 2. 重号 ----------
    dup: dict[int, list] = defaultdict(list)
    for (r, c), info in cells.items():
        if info.get("expect") in (PREV, NEXT):
            continue
        v = info.get("value")
        if v is not None and 1 <= v <= days:
            dup[v].append((r, c))
    for v, lst in dup.items():
        if len(lst) > 1:
            others = [x for x in lst if x not in wrong]
            add("重号", "error", f"{month} 月出现了 {len(lst)} 个「{v:02d}」",
                cell=(others or lst)[-1])

    # ---------- 3. 超出本月天数 ----------
    for (r, c), info in cells.items():
        if info.get("expect") in (PREV, NEXT):
            continue
        v = info.get("value")
        if v is not None and v > days:
            add("超范围", "error",
                f"{month} 月只有 {days} 天，但网格里印了 {v:02d}", cell=(r, c))

    # ---------- 4. 星期对位 ----------
    if layout == "linear":
        pass
    else:
        first = FIRST_COL[anchor]
        expect_first = (truth["first_wd"] - first) % 7
        bad_week = []
        for (r, c), info in cells.items():
            if info.get("expect") in (PREV, NEXT):
                continue
            v = info.get("value")
            if v is None or not (1 <= v <= days) or (r, c) in wrong:
                continue
            if not info.get("pos_ok", True):
                add("待确认", "warn",
                    f"{month} 月 {v} 日 画在格子对角处，机器判断不了它落在哪一列",
                    cell=(r, c), note="最后一周常这样排，建议人工看一眼")
                continue
            col_wd = (first + c) % 7
            true_wd = (truth["first_wd"] + v - 1) % 7
            if col_wd != true_wd:
                bad_week.append((v, c, col_wd, true_wd))

        if c0 != expect_first:
            add("整月错位", "error",
                f"{month} 月整月偏了一列：1 号被排在从左数第 {c0 + 1} 列，"
                f"而按历法它应在第 {expect_first + 1} 列"
                f"（{truth['year']} 年 {month} 月 1 日是{WEEKDAY_FULL[truth['first_wd']]}）",
                cell=(r0, c0))
        elif len(bad_week) >= 7:
            add("整月错位", "error",
                f"{month} 月有 {len(bad_week)} 个日期不在正确的星期列上", cell=(r0, c0))
        else:
            for v, c, col_wd, true_wd in bad_week:
                add("星期错位", "error",
                    f"{month} 月 {v} 日（{WEEKDAY_FULL[true_wd]}）被排在了「{WEEKDAY_FULL[col_wd]}」那一列",
                    cell=cell_of(v))

    # ---------- 5. 行数 ----------
    used = sorted({r for (r, _c), i in cells.items()
                   if i.get("expect") is not None and i.get("expect") not in (PREV, NEXT)})
    if used and layout != "linear":
        n_used = used[-1] - used[0] + 1
        if n_used != truth["weeks"]:
            add("行数", "info",
                f"{month} 月排了 {n_used} 行，按历法需要 {truth['weeks']} 行")
        if n_used > truth["weeks"]:
            add("多余行", "warn",
                f"{month} 月用到了 {n_used} 行，最后一行的日期可能超出版面")

    # ---------- 6. 分组不确定的地方 ----------
    for (r, c), info in cells.items():
        if info.get("expect") in (PREV, NEXT):
            continue
        if info.get("value") is None:
            continue
        if info.get("score", 1.0) < MIN_CONF:
            add("待确认", "warn",
                f"第 {r - r0 + 1} 行第 {c - c0 + 1} 列这一格的字形分组不确定，机器可能看错",
                cell=(r, c), note="建议人工看一眼这一格")

    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]
    return {
        "page": page_no,
        "month": month,
        "ok": not errors,
        "findings": findings,
        "n_error": len(errors),
        "n_warn": len(warns),
        "cells": cells,
        "truth": truth,
        "anchor": anchor,
        "r0": r0,
        "c0": c0,
    }
