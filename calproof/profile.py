"""版式档案（profile）：把"这一款台历怎么读"存下来，下次直接套用。

为什么要有这个东西
------------------
七列网格、横排一行、横排两行这些常见版式，靠通用判据就能认。
但个性化台历的版式是开放集合：连排换行、环形、异形……每来一款就要改一次规则。

所以改成两段式：
  1. **学过**（learn_flow）：在"行 / 步距"上做小范围参数搜索；
  2. **存档**（save）：把几何参数（相对页面尺寸的比例）写进 profiles/*.json；
  3. **命中**（match + read_by_profile）：同一款式再印时直接按档案切字。

存的是**几何**不是截图：换照片、换文案、换开数（按比例缩放）都能命中。
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.dirname(HERE), "profiles")


def profiles_dir() -> str:
    return os.environ.get("CALPROOF_PROFILES") or DEFAULT_DIR


def load_all() -> list[dict]:
    d = profiles_dir()
    out: list[dict] = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                p = json.load(f)
            if p.get("rows"):
                out.append(p)
        except Exception:
            continue
    return out


def save(prof: dict, name: str | None = None) -> str:
    d = profiles_dir()
    os.makedirs(d, exist_ok=True)
    prof = dict(prof)
    prof.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
    stem = name or prof.get("name") or "profile"
    path = os.path.join(d, stem + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=1)
    return path


def _aspect(prof: dict) -> float:
    return float(prof["page_w"]) / float(prof["page_h"])


def match(profiles: list[dict], page_w: float, page_h: float) -> dict | None:
    """按"长宽比 + 尺寸"找最像的一款。差太多的不认，宁可认不出来。"""
    best, best_d = None, 9e9
    for p in profiles:
        a = _aspect(p)
        a_now = page_w / page_h
        da = abs(a - a_now) / max(a_now, 1e-6)
        ds = abs(float(p["page_w"]) - page_w) / max(page_w, 1e-6)
        if da > 0.03 and ds > 0.05:
            continue
        d = da * 3.0 + ds
        if d < best_d:
            best, best_d = p, d
    return best

# ---------------------------------------------------------------- 按档案读页

def _pattern_ok(cells: dict, ndays: int) -> bool:
    """几何自检：日期的"字形个数"必须符合历法常识。

    1~9 号是一位数（每格 1 个字），10 号以后是两位数（每格 2 个字）。
    农历小字、星期字、正文都凑不出这个"先 1 后 2"的整齐序列。
    """
    keys = sorted(cells, key=lambda k: (k[0], k[1]))
    counts = [len(cells[k]) for k in keys]
    if any(c == 0 for c in counts):
        return False
    if counts == [2] * ndays:                 # 补零排版 01、02……09
        return True
    return counts == [1] * min(9, ndays) + [2] * max(0, ndays - 9)


def read_by_profile(img, items, prof: dict, ndays: int, glyphs=None) -> dict:
    """照档案把这一页切成日期槽。切不出正好 ndays 个、或者不像日期，就老实失败。"""
    if glyphs is None:
        from .layers import glyphs_of_page
        h = float(getattr(getattr(img, "rect", None), "height", prof["page_h"]))
        glyphs = glyphs_of_page(img, items, h)
    w = float(prof["page_w"])
    h = float(prof["page_h"])
    gh = float(prof.get("glyph_h_frac") or 0.02) * h
    for tol_k in (0.8, 1.2, 1.6):
        out = _read_once(prof, glyphs, w, h, gh, ndays, tol_k)
        if out:
            return out
    return {"ok": False, "reason": "按档案切出来的字不像日期（位置或字号对不上）",
            "glyphs": glyphs}


def _read_once(prof, glyphs, w, h, gh, ndays, tol_k):
    """只信档案里的两件事：**日期行在哪（y）**、**一格多宽（pitch）**。

    至于这一行从哪一列开始，不写死 —— 连排版式月初会留空（1 号排在周五那格），
    写死绝对位置的话，换个年份就会整体错位。从左往右现切，顺序天然是对的。
    """
    cells: dict[tuple[int, int], list] = {}
    boxes: dict[tuple[int, int], tuple] = {}
    index: dict[tuple[int, int], int] = {}
    k = 0
    left = ndays
    for r, row in enumerate(prof["rows"]):
        if left <= 0:
            break
        yc = float(row["y_frac"]) * h
        pitch = float(row["pitch_frac"]) * w
        cap = int(row["slots"])
        band = sorted((g for g in glyphs if abs(g.cy - yc) <= tol_k * gh),
                      key=lambda g: g.cx)
        if not band:
            return None
        want = min(cap, left)
        groups, best_d = None, 1e9
        for thr in _thr_cands(band):
            cand = _segment(band, thr)
            if not (want - 2 <= len(cand) <= cap + 1):
                continue
            d = abs(len(cand) - want)
            if d < best_d:
                groups, best_d = cand, d
        if groups is None:
            return None
        groups = groups[:want]
        y0 = min(g.y0 for g in band)
        y1 = max(g.y1 for g in band)
        for i, s in enumerate(groups):
            cx = float(np.median([g.cx for g in s]))
            cells[(r, i)] = s
            boxes[(r, i)] = (cx - 0.5 * pitch, y0, cx + 0.5 * pitch, y1)
            index[(r, i)] = k
            k += 1
        left -= len(groups)

    if len(cells) != ndays:
        return None
    if not _pattern_ok(cells, ndays):
        return None
    alive = [g for v in cells.values() for g in v]
    if not alive:
        return None

    from .cluster import cluster_shapes
    labels, _c, _s, _n = cluster_shapes([g.mask for g in alive])
    return {"ok": True, "kind": "linear", "rows_n": len(prof["rows"]),
            "cells": cells, "boxes": boxes, "index": index, "wd_boxes": {},
            "alphabet": len(set(labels)), "glyphs": glyphs, "n_slots": len(cells),
            "profile": prof.get("name", "")}

# ---------------------------------------------------------------- 参数搜索学习

def _bands(gl, tol):
    if not gl:
        return []
    o = sorted(gl, key=lambda g: g.cy)
    out = [[o[0]]]
    for g in o[1:]:
        if g.cy - out[-1][-1].cy <= tol:
            out[-1].append(g)
        else:
            out.append([g])
    return out


def _segment(band, thr):
    o = sorted(band, key=lambda g: g.cx)
    groups = [[o[0]]]
    for prev, g in zip(o, o[1:]):
        if g.cx - prev.cx < thr:
            groups[-1].append(g)
        else:
            groups.append([g])
    return groups


def _thr_cands(band) -> list[float]:
    """切分阈值候选：直接取"两个相邻间隙之间的中点"。

    为什么不再用"步距 × 系数"：一整行全是两位数时（19~31），
    字内部空隙（8~9）和格与格之间（11~14）只差一点点，
    步距本身也不是间隙，乘系数永远卡不准。
    间隙是分两堆的，阈值落在两堆中间就一定切对。
    """
    o = sorted(band, key=lambda g: g.cx)
    gaps = sorted({round(o[i + 1].cx - o[i].cx, 2) for i in range(len(o) - 1)})
    if len(gaps) < 3:
        return []
    thrs = [gaps[0] * 0.95]
    thrs += [round((a + b) / 2.0, 2) for a, b in zip(gaps, gaps[1:]) if b - a >= 0.15]
    thrs.append(gaps[-1] * 1.05)
    return thrs


def _center_pitch(gs) -> float:
    mids = [float(np.median([g.cx for g in s])) for s in gs]
    if len(mids) < 2:
        return 0.0
    return float(np.median([mids[i + 1] - mids[i] for i in range(len(mids) - 1)]))


def _slot_ok(groups, pitch) -> bool:
    """切得对不对：每槽 1~2 个字，槽宽明显小于格距，而且格距要匀。"""
    if not groups or pitch <= 0:
        return False
    for s in groups:
        if len(s) > 2:
            return False
        if (max(g.x1 for g in s) - min(g.x0 for g in s)) > 0.95 * pitch:
            return False
    mids = [float(np.median([g.cx for g in s])) for s in groups]
    if len(mids) >= 3:
        gaps = [mids[i + 1] - mids[i] for i in range(len(mids) - 1)]
        med = float(np.median(gaps))
        if med <= 0:
            return False
        if float(np.std(gaps)) > 0.14 * med:
            return False
    return True


def learn_flow(img, items, page_w: float, page_h: float, ndays: int, glyphs=None) -> dict:
    """不认识这一页时：在"哪几行是日期行 / 每行步距多少"上做小范围搜索。

    只要能找到一组"切出来正好等于当月天数"的行 + 步距组合，这一页就认出来了。
    """
    if glyphs is None:
        from .layers import glyphs_of_page
        glyphs = glyphs_of_page(img, items, page_h)
    if len(glyphs) < 40:
        return {"ok": False, "reason": "字形太少"}

    gh = float(np.median([g.h for g in glyphs]))
    bands = [b for b in _bands(glyphs, tol=0.55 * gh) if len(b) >= 8]
    if not bands:
        return {"ok": False, "reason": "没有凑出足够大的横向字带"}

    opts = {}
    for bi, b in enumerate(bands):
        cands = []
        for thr in _thr_cands(b):
            gs = _segment(b, thr)
            if not (4 <= len(gs) <= 40):
                continue
            pitch = _center_pitch(gs)
            if _slot_ok(gs, pitch):
                cands.append((len(gs), pitch, gs))
        if cands:
            opts[bi] = cands

    if not opts:
        return {"ok": False, "reason": "没有一条字带能干净切开"}

    idxs_sorted = sorted(opts)
    best = None
    from itertools import combinations, product
    for k in (1, 2, 3):
        for combo in combinations(idxs_sorted, k):
            span_y = bands[combo[-1]][0].cy - bands[combo[0]][0].cy
            if span_y > 0.45 * page_h:
                continue
            for a, b2 in zip(combo, combo[1:]):
                if bands[b2][0].cy - bands[a][0].cy > 6.0 * gh:
                    continue
            picks = [opts[i][:4] for i in combo]
            for choice in product(*picks):
                total = sum(c[0] for c in choice)
                if total != ndays:
                    continue
                if any(c[0] < 4 for c in choice):
                    continue
                pitches = [c[1] for c in choice]
                med = float(np.median(pitches))
                if med <= 0 or max(abs(p - med) for p in pitches) > 0.35 * med:
                    continue
                score = float(sum(abs(c[0] - 11) for c in choice)) / len(choice)
                cand = (len(combo), round(score, 3), list(zip(combo, choice)))
                if best is None or cand[:2] < best[:2]:
                    best = cand
    if best is None:
        return {"ok": False, "reason": f"参数搜索也没找到正好 {ndays} 个槽的切法"}

    rows = sorted(best[2], key=lambda t: bands[t[0]][0].cy)
    cells, boxes, index = {}, {}, {}
    k = 0
    for r, (bi, choice) in enumerate(rows):
        n, pitch, gs = choice
        b = bands[bi]
        yc = float(np.median([g.cy for g in b]))
        y0, y1 = min(g.y0 for g in b), max(g.y1 for g in b)
        mids = [float(np.median([g.cx for g in s])) for s in gs]
        for i, s in enumerate(gs):
            cells[(r, i)] = s
            boxes[(r, i)] = (mids[i] - 0.5 * pitch, y0, mids[i] + 0.5 * pitch, y1)
            index[(r, i)] = k
            k += 1

    if not _pattern_ok(cells, ndays):
        return {"ok": False, "reason": "切出来的字不像日期（一位/两位的分布不对），装不进历法"}

    labels = [i for i in range(len(rows))]
    row_glyphs = [g for (bi, _c) in rows for g in bands[bi]]
    row_h = float(np.median([g.h for g in row_glyphs])) if row_glyphs else gh
    prof = {
        "name": "",
        "kind": "flow",
        "page_w": round(float(page_w), 2),
        "page_h": round(float(page_h), 2),
        "glyph_h_frac": round(row_h / float(page_h), 5),
        "rows": [],
    }
    for r, (bi, choice) in enumerate(rows):
        n, pitch, gs = choice
        b = bands[bi]
        prof["rows"].append({
            "y_frac": round(float(np.median([g.cy for g in b]) / float(page_h)), 5),
            "slots": int(n),
            "x0_frac": round(float(min(g.x0 for g in b)) / float(page_w), 5),
            "pitch_frac": round(float(pitch) / float(page_w), 5),
        })

    from .cluster import cluster_shapes
    alive = [g for v in cells.values() for g in v]
    lab, _c, _s, _n = cluster_shapes([g.mask for g in alive])
    return {"ok": True, "kind": "linear", "rows_n": len(rows),
            "cells": cells, "boxes": boxes, "index": index, "wd_boxes": {},
            "alphabet": len(set(lab)), "glyphs": glyphs, "n_slots": len(cells),
            "learned_profile": prof, "profile": "(新学的)"}
