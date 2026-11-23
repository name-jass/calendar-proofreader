"""横排（线性）台历：一行或两行，日期从左到右连着排 1、2、3…

和七列网格版式的区别：这里没有"星期"，也没有"列"，
每个日期自己就是一个格位（槽），星期由槽上方那个字决定。

判据仍然是"位置 + 顺序"：
    槽数必须等于当月天数、日期必须从左到右连续，
    至于每格长什么样，交给形状分组 + 位置投票去认。
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .cluster import cluster_shapes
from .extract import Glyph, Item, PageImage
from .layers import date_glyphs, glyphs_of_page


def _bands(glyphs: list[Glyph], tol: float) -> list[list[Glyph]]:
    """按 y 把字形分成横条（星期字 / 日期 / 农历 各一条）。"""
    if not glyphs:
        return []
    ordered = sorted(glyphs, key=lambda g: g.cy)
    out: list[list[Glyph]] = [[ordered[0]]]
    for g in ordered[1:]:
        if g.cy - out[-1][-1].cy <= tol:
            out[-1].append(g)
        else:
            out.append([g])
    return out


def _slots(gs: list[Glyph]) -> tuple[list[list[Glyph]], float]:
    """把一条横条切成槽：间距明显小于正常步距的，算同一个槽（两位数的两个数字）。"""
    if not gs:
        return [], 0.0
    ordered = sorted(gs, key=lambda g: g.cx)
    gaps = [ordered[i + 1].cx - ordered[i].cx for i in range(len(ordered) - 1)]
    pitch = float(np.median(gaps)) if gaps else 0.0
    if pitch <= 0:
        return [ordered], 0.0
    groups: list[list[Glyph]] = [[ordered[0]]]
    for i, g in enumerate(ordered[1:]):
        if g.cx - ordered[i].cx < 0.55 * pitch:
            groups[-1].append(g)
        else:
            groups.append([g])
    return groups, pitch


def _digit_band(band: list[Glyph]) -> tuple[int, list[int]]:
    """这条横条像不像"日期"：像就返回 (字母表大小, 聚类标签)。"""
    if len(band) < 12:
        return 99, []
    labels, _c, _s, _n = cluster_shapes([g.mask for g in band])
    return len(set(labels)), labels


def _quality(slots: list[list[Glyph]], alpha: int) -> tuple:
    """给候选横带打个分，用来在"日期那一行"和"星期字那一行"之间选对。"""
    ws = [float(np.median([g.w for g in s])) for s in slots if s]
    if not ws:
        return (0, -99, 0)
    med = float(np.median(ws))
    spread = float(np.std(ws)) / (med + 1e-6)
    return (1 if spread > 0.10 else 0, -abs(alpha - 11), len(slots))


def page_linear(img: PageImage, items: list[Item], page_h: float, ndays: int,
                glyphs: list[Glyph] | None = None) -> dict:
    """试着按"横排台历"解释这一页。返回结构同 page_structure（多一个 kind）。

    门槛故意设得严：**一条横带里必须有 ≥10 个等距槽**。
    七列网格版式的一行最多 7 个日期，凑不到这个数，
    所以这道门槛能防止把普通月历误判成横排版式。
    """
    if glyphs is None:
        glyphs = glyphs_of_page(img, items, page_h)
    if len(glyphs) < 60:
        return {"ok": False, "reason": "这一页的字形太少，不是月历版式（封面 / 广告页）",
                "glyphs": glyphs}

    h = float(np.median([g.h for g in glyphs]))
    bands = [b for b in _bands(glyphs, tol=0.55 * h) if len(b) >= 12]

    cands = []
    for b in bands:
        slots, pitch = _slots(b)
        if len(slots) < 10:
            continue
        alpha, _lab = _digit_band(b)
        if not (9 <= alpha <= 24):
            continue
        if abs(len(slots) - ndays) > 2:
            continue
        cands.append((b, slots, pitch, alpha))

    if not cands:
        return {"ok": False, "reason": "没有找到「一条横带里 ≥10 个等距槽」的版式",
                "glyphs": glyphs}

    cands.sort(key=lambda t: _quality(t[1], t[3]), reverse=True)
    row_h = float(np.median([g.h for c in cands for g in c[0]]))
    rows = [cands[0]]
    total = len(rows[0][1])
    while abs(total - ndays) > 1:
        best = None
        for c2 in cands:
            if c2 in rows:
                continue
            gap = min(abs(c2[0][0].cy - r[0][-1].cy) for r in rows)
            if gap > 3.0 * row_h:
                continue
            if abs(total + len(c2[1]) - ndays) < abs(total - ndays):
                if best is None or len(c2[1]) > len(best[1]):
                    best = c2
        if best is None:
            break
        rows.append(best)
        total += len(best[1])

    rows.sort(key=lambda c: c[0][0].cy)

    cells: dict[tuple[int, int], list[Glyph]] = {}
    boxes: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    index: dict[tuple[int, int], int] = {}
    k = 0
    wd_boxes: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for r, (_b, slots, pitch, _alpha) in enumerate(rows):
        b = _b
        y0 = min(g.y0 for g in b)
        y1 = max(g.y1 for g in b)
        mids = [(min(g.x0 for g in s) + max(g.x1 for g in s)) / 2.0 for s in slots]
        # 用"槽中心之间的中位步距"当尺子，补空槽时不会多插格
        if len(mids) > 2:
            step = float(np.median([mids[i + 1] - mids[i] for i in range(len(mids) - 1)]))
        else:
            step = pitch

        seq: list[tuple[list[Glyph] | None, float]] = []
        for i, s in enumerate(slots):
            if i > 0 and step > 0:
                gap = mids[i] - mids[i - 1]
                n_miss = int(round(gap / step)) - 1
                if 1 <= n_miss <= 2:
                    for j in range(n_miss):
                        seq.append((None, mids[i - 1] + step * (j + 1)))
            seq.append((s, mids[i]))

        w_slot = float(np.median([max(g.x1 for g in s) - min(g.x0 for g in s) for s in slots])) \
            if slots else 6.0
        for c, (s, cx) in enumerate(seq):
            cells[(r, c)] = s or []
            boxes[(r, c)] = (cx - w_slot / 2.0, y0, cx + w_slot / 2.0, y1)
            index[(r, c)] = k
            k += 1
        n_slots_row = len(seq)

        up = None
        for b2 in bands:
            if b2[0].cy < b[0].cy and b[0].cy - b2[0].cy < 3.0 * row_h:
                up = b2
        if up is not None and len(up) >= 0.8 * n_slots_row:
            ups, _p = _slots(up)
            if len(ups) >= n_slots_row:
                for c in range(n_slots_row):
                    wd_boxes[(r, c)] = (
                        min(g.x0 for g in ups[c]),
                        min(g.y0 for g in up),
                        max(g.x1 for g in ups[c]),
                        max(g.y1 for g in up),
                    )

    n = len(cells)
    if abs(n - ndays) > 1:
        return {"ok": False,
                "reason": f"没切出对应天数的槽（切到 {n} 个，这一月是 {ndays} 天）",
                "glyphs": glyphs, "n_slots": n}

    alive = [g for v in cells.values() for g in date_glyphs(v)]
    labels, _c, _s, _n = cluster_shapes([g.mask for g in alive])

    return {
        "ok": True,
        "kind": "linear",
        "digit_h": row_h,
        "rows_n": len(rows),
        "cells": cells,
        "boxes": boxes,
        "index": index,
        "wd_boxes": wd_boxes,
        "alphabet": len(set(labels)),
        "glyphs": glyphs,
        "n_slots": n,
    }
