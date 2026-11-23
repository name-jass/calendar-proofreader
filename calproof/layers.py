"""找出这一页里"哪一批字是日期"，并把它们还原成七列网格。

难点：一个格子里同时有日期（8.4pt）和农历小字（7.1pt），只差一点点，
按字号分很容易混。所以这里换了个判据：
    日期那一层只有 10 种形状（0~9），农历那一层有几十种形状。
"字母表最小"的那一层，就是日期。这个判据不需要知道字体。
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .cluster import cluster_shapes
from .extract import Glyph, Item, PageImage

def _cluster1d(vals: list[float], tol: float) -> list[float]:
    if not vals:
        return []
    v = sorted(vals)
    groups = [[v[0]]]
    for x in v[1:]:
        if x - groups[-1][-1] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [float(np.mean(g)) for g in groups]

def _group_by_height(glyphs: list[Glyph], gap: float) -> list[list[Glyph]]:
    """按字高分层。层与层之间的空隙要够大，否则算同一层。"""
    if not glyphs:
        return []
    ordered = sorted(glyphs, key=lambda g: g.h)
    groups: list[list[Glyph]] = [[ordered[0]]]
    for g in ordered[1:]:
        if g.h - groups[-1][-1].h <= gap:
            groups[-1].append(g)
        else:
            groups.append([g])
    return groups

def _prune_rows(cells: dict, rows: list[float]) -> tuple[dict, list[float]]:
    """剔掉星期表头那一行。

    表头（SUN MON…）字高常常和日期几乎一样，按字号分不开；
    但日期行是等距的，表头那一行和第一行日期的间距会明显偏小。
    所以用"行距是否规律"来判断，而不是用字号。
    注意：不要再按"格子数太多"来砍行 —— 最后一周压在对角格里的月份
    格子本来就多，那样会把合法的第一行砍掉。
    """
    cells = dict(cells)
    rows = list(rows)
    while len(rows) > 1:
        used = {r for (r, _c) in cells}
        gaps = [rows[i + 1] - rows[i] for i in range(len(rows) - 1)]
        pitch = float(np.median(gaps)) if gaps else 0.0
        top_gap = gaps[0]
        irregular = pitch > 0 and top_gap < 0.92 * pitch
        if not irregular:
            break
        if 0 not in used:
            rows.pop(0)
            continue
        cells = {(r - 1, c): v for (r, c), v in cells.items() if r != 0}
        rows.pop(0)
    return cells, rows

def _lattice(glyphs: list[Glyph], h: float, passes: int = 2):
    """把字形摆到七列网格上。两遍：第一遍可能把"星期表头"也算进来。"""
    cols, rows, cells = _once(glyphs, h)
    for _ in range(passes - 1):
        alive = [g for v in cells.values() for g in v]
        if len(alive) < 8:
            break
        h2 = float(np.median([g.h for g in alive]))
        cols, rows, cells = _once(alive, h2)
    return cols, rows, cells

def _pick_window(centers: list[float], counts: dict, k: int) -> list[int]:
    """在候选行列里挑出 k 个"间距最规律、字数最多"的连续项。"""
    n = len(centers)
    if n <= k:
        return list(range(n))
    gaps = [centers[i + 1] - centers[i] for i in range(n - 1)]
    pitch = float(np.median(gaps)) if gaps else 1.0
    best = None
    for s in range(0, n - k + 1):
        seg = centers[s:s + k]
        dev = sum(abs((seg[i + 1] - seg[i]) - pitch) for i in range(k - 1))
        total = sum(counts.get(s + i, 0) for i in range(k))
        sc = total - 1.2 * dev
        if best is None or sc > best[0]:
            best = (sc, s)
    if best is None:
        return list(range(n))
    return list(range(best[1], best[1] + k))

def _merge_close(centers: list[float], weights: list[int], ratio: float = 0.55):
    """把贴得太近的两个簇并成一个（一列被切成两列的情况）。"""
    cs = list(centers)
    ws = list(weights)
    while len(cs) > 1:
        gaps = [cs[i + 1] - cs[i] for i in range(len(cs) - 1)]
        pitch = float(np.median(gaps))
        hit = None
        for i, g in enumerate(gaps):
            if g < ratio * pitch:
                hit = i
                break
        if hit is None:
            break
        tot = ws[hit] + ws[hit + 1]
        if tot > 0:
            cs[hit] = (cs[hit] * ws[hit] + cs[hit + 1] * ws[hit + 1]) / tot
        else:
            cs[hit] = (cs[hit] + cs[hit + 1]) / 2.0
        ws[hit] = tot
        del cs[hit + 1]
        del ws[hit + 1]
    return cs

def _column_centers(glyphs: list[Glyph], h: float) -> list[float]:
    """估七列的位置（先用"字最多的那几行"当样本再聚类）。"""
    def nearest(vals, centers):
        return [int(np.argmin([abs(v - c) for c in centers])) for v in vals]

    row_bands = _cluster1d([g.cy for g in glyphs], tol=0.55 * h)
    ri = nearest([g.cy for g in glyphs], row_bands)
    cnt_row = Counter(ri)
    dense = [g for g, i in zip(glyphs, ri) if cnt_row[i] >= 5]
    src = dense if len(dense) >= 20 else glyphs

    cols = _cluster1d([g.cx for g in src], tol=0.95 * h)
    if len(cols) > 7:
        ci = nearest([g.cx for g in src], cols)
        cnt = Counter(ci)
        top = sorted(range(len(cols)), key=lambda i: (-cnt.get(i, 0), i))[:7]
        cols = sorted(cols[i] for i in top)
    return cols

def _once(glyphs: list[Glyph], h: float):
    cols = _column_centers(glyphs, h)
    rows0 = _cluster1d([g.cy for g in glyphs], tol=0.55 * h)

    def nearest(vals, centers):
        return [int(np.argmin([abs(v - c) for c in centers])) for v in vals]

    def counts_of(idx, n):
        c = Counter(idx)
        return [c.get(i, 0) for i in range(n)]

    if len(cols) > 1:
        cols = _merge_close(cols, counts_of(nearest([g.cx for g in glyphs], cols), len(cols)))
    rows = _merge_close(rows0, counts_of(nearest([g.cy for g in glyphs], rows0), len(rows0)))

    if not cols:
        return [], [], {}

    if len(cols) > 1:
        pitch = float(np.median([cols[i + 1] - cols[i] for i in range(len(cols) - 1)]))
    else:
        pitch = 0.0
    lo = cols[0] - 0.55 * pitch if pitch else -1e9
    hi = cols[-1] + 0.55 * pitch if pitch else 1e9

    ci = nearest([g.cx for g in glyphs], cols)
    ri = nearest([g.cy for g in glyphs], rows)

    tmp = [(c, r, g) for c, r, g in zip(ci, ri, glyphs) if lo <= g.cx <= hi]
    ci = [t[0] for t in tmp]
    ri = [t[1] for t in tmp]
    glyphs = [t[2] for t in tmp]

    cnt_r = Counter(ri)
    keep = _pick_window(rows, cnt_r, min(7, len(rows)))
    remap = {old: new for new, old in enumerate(keep)}
    tmp = [(c, remap[r], g) for c, r, g in zip(ci, ri, glyphs) if r in remap]
    ci = [t[0] for t in tmp]
    ri = [t[1] for t in tmp]
    glyphs = [t[2] for t in tmp]
    rows = [rows[i] for i in keep]

    cells: dict[tuple[int, int], list[Glyph]] = {}
    for c, r, g in zip(ci, ri, glyphs):
        cells.setdefault((r, c), []).append(g)
    for k in cells:
        cells[k].sort(key=lambda g: g.x0)

    cells, rows = _prune_rows(cells, rows)

    used_c = sorted({c for (_r, c) in cells})
    used_r = sorted({r for (r, _c) in cells})
    rc = {old: new for new, old in enumerate(used_c)}
    rr = {old: new for new, old in enumerate(used_r)}
    cells = {(rr[r], rc[c]): v for (r, c), v in cells.items()}

    col_acc: dict[int, list[float]] = {}
    row_acc: dict[int, list[float]] = {}
    for (r, c), gs in cells.items():
        col_acc.setdefault(c, []).extend(g.cx for g in gs)
        row_acc.setdefault(r, []).extend(g.cy for g in gs)
    cols = [float(np.mean(col_acc[c])) for c in sorted(col_acc)]
    rows = [float(np.mean(row_acc[r])) for r in sorted(row_acc)]
    return cols, rows, cells

def _score(cols, rows, cells, alphabet: int) -> float:
    """给这一层打分：像不像"一个月的日期"。"""
    n = len(cells)
    s = 0.0
    s += 5.0 if len(cols) == 7 else -5.0
    s += 3.0 if 4 <= len(rows) <= 7 else -3.0
    if 28 <= n <= 34:
        s += 6.0
    elif 20 <= n < 28:
        s -= 2.0
    else:
        s -= 7.0
    if 9 <= alphabet <= 11:
        s += 5.0
    elif 12 <= alphabet <= 18:
        s += 1.0
    else:
        s -= 4.0
    return s

def glyphs_of_page(img: PageImage, items: list[Item], page_h: float) -> list[Glyph]:
    """把一页里的矢量路径切成"像字"的字形（网格版式与横排版式共用）。"""
    glyphs: list[Glyph] = []
    for it in items:
        if it.h < page_h * 0.004 or it.h > page_h * 0.98:
            continue
        for g in img.split_item(it):
            if g.h < page_h * 0.004 or g.h > page_h * 0.12:
                continue          # 太大的不是字（照片块）
            if g.w < 0.12 * g.h or g.w > page_h * 0.10:
                continue          # 细长条是装饰线，超宽的也不是字
            if int(g.mask.sum()) < 0.8 * g.w * g.h:
                continue
            glyphs.append(g)
    return glyphs

def _cluster_vals(vals: list[float], tol: float) -> list[float]:
    """把一串坐标并成若干个中心（相差小于 tol 的算同一个）。"""
    if not vals:
        return []
    v = sorted(vals)
    g = [[v[0]]]
    for x in v[1:]:
        if x - g[-1][-1] <= tol:
            g[-1].append(x)
        else:
            g.append([x])
    return [float(np.mean(x)) for x in g]

def structure_from_boxes(page, glyphs: list[Glyph]) -> dict | None:
    """用版面上画好的"格子框"定位七列网格（设计好的台历基本都是这样）。"""
    try:
        drawings = page.get_drawings()
    except Exception:
        return None

    cand = []
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        if 18 <= r.width <= 220 and 8 <= r.height <= 140:
            cand.append((round(r.width), round(r.height), r))
    if len(cand) < 15:
        return None

    c = Counter((w, h) for w, h, _r in cand)
    (bw, bh), n_boxes = c.most_common(1)[0]
    if n_boxes < 15:
        return None

    boxes = [r for w, h, r in cand if abs(w - bw) <= 1.5 and abs(h - bh) <= 1.5]
    xs = _cluster_vals([b.x0 for b in boxes], tol=4.0)
    ys = _cluster_vals([b.y0 for b in boxes], tol=4.0)
    if len(xs) != 7 or not (3 <= len(ys) <= 7):
        return None

    def idx_of(v, centers):
        return int(np.argmin([abs(v - c2) for c2 in centers]))

    rect_of: dict[tuple[int, int], tuple] = {}
    for b in boxes:
        key = (idx_of(b.y0, ys), idx_of(b.x0, xs))
        old = rect_of.get(key)
        if old is None or (b.width * b.height) > ((old[2] - old[0]) * (old[3] - old[1])):
            rect_of[key] = (b.x0, b.y0, b.x1, b.y1)
    if len(rect_of) < 20:
        return None

    cells: dict[tuple[int, int], list[Glyph]] = {}
    for g in glyphs:
        if g.h > 0.62 * bh or g.w > 0.62 * bw:
            continue
        hit = None
        for key, (x0, y0, x1, y1) in rect_of.items():
            if x0 - 1.5 <= g.cx <= x1 + 1.5 and y0 - 1.5 <= g.cy <= y1 + 1.5:
                hit = key
                break
        if hit is not None:
            cells.setdefault(hit, []).append(g)
    for k in cells:
        cells[k].sort(key=lambda g2: (round(g2.y0, 1), g2.x0))

    live = {k: v for k, v in cells.items() if v}
    if len(live) < 20:
        return None

    dg = [g for v in live.values() for g in date_glyphs(v)]
    labels = cluster_shapes([g.mask for g in dg])[0] if dg else []
    return {
        "ok": True,
        "digit_h": float(np.median([g.h for g in dg])) if dg else 0.0,
        "cols": xs,
        "rows": ys,
        "cells": live,
        "boxes": rect_of,
        "alphabet": len(set(labels)),
        "trials": [],
        "glyphs": glyphs,
        "kind": "grid",
    }

def page_structure(img: PageImage, items: list[Item], page_h: float, page=None,
                  glyphs: list[Glyph] | None = None) -> dict:
    """返回这一页的日期层网格；找不到就返回 {'ok': False, 'reason': ...}。"""
    if glyphs is None:
        glyphs = glyphs_of_page(img, items, page_h)

    if len(glyphs) < 40:
        return {"ok": False, "reason": "这一页的字形太少，不是月历版式（封面 / 广告页）",
                "glyphs": glyphs}

    if page is not None:
        st = structure_from_boxes(page, glyphs)
        if st:
            return st

    layers = [g for g in _group_by_height(glyphs, gap=0.60) if len(g) >= 15]
    if not layers:
        return {"ok": False, "reason": "没有找到成规模的字号层", "glyphs": glyphs}

    best = None
    trials = []
    for layer in layers:
        h = float(np.median([g.h for g in layer]))
        narrow = [g for g in layer if g.w <= 0.85 * g.h]
        source = narrow if len(narrow) >= 20 else list(layer)
        cols, rows, cells = _lattice(source, h)
        if not cells:
            continue
        alive = [g for v in cells.values() for g in date_glyphs(v)]
        labels, _, _, _ = cluster_shapes([g.mask for g in alive])
        alphabet = len(set(labels))
        sc = _score(cols, rows, cells, alphabet)
        trials.append({"h": h, "cols": len(cols), "rows": len(rows),
                       "cells": len(cells), "alphabet": alphabet, "score": sc})
        if best is None or sc > best["score"]:
            best = {"score": sc, "h": h, "cols": cols, "rows": rows,
                    "cells": cells, "alphabet": alphabet, "labels": labels,
                    "layer": layer}

    if best is None:
        return {"ok": False, "reason": "网格还原失败", "glyphs": glyphs}

    if best["score"] < 4 or len(best["cols"]) != 7 or len(best["cells"]) < 20:
        return {"ok": False, "reason": "这一页没还原出七列月历网格", "glyphs": glyphs,
                "trials": trials}

    cells = {k: v for k, v in best["cells"].items() if 1 <= len(v) <= 10}
    if not cells:
        return {"ok": False, "reason": "格子里的字形数量异常", "glyphs": glyphs}

    return {
        "ok": True,
        "digit_h": best["h"],
        "cols": best["cols"],
        "rows": best["rows"],
        "cells": cells,
        "alphabet": best["alphabet"],
        "trials": trials,
        "glyphs": glyphs,
    }

PREV = -1          # 上个月末尾的日期，对账时整格忽略
NEXT = -2          # 下个月开头的日期，同样整格忽略

MAX_DIGITS = 2     # 一个月最多 31，日期最多两位

def _split_pair(g: Glyph) -> list[Glyph]:
    """把"两个数字粘成一个"的字形（如 31、25）从中间的墨缝切开。"""
    if g.mask is None or g.mask.size == 0:
        return [g]
    h_, w_ = g.mask.shape
    if w_ < 6 or h_ < 6:
        return [g]
    if w_ / float(h_) < 0.95:
        return [g]
    ink = g.mask.sum(axis=0)
    peak = float(ink.max()) if ink.size else 0.0
    if peak <= 0:
        return [g]
    lo, hi = max(1, int(w_ * 0.32)), min(w_ - 1, int(w_ * 0.68))
    if hi <= lo:
        return [g]
    cut = int(np.argmin(ink[lo:hi])) + lo
    if float(ink[cut]) > 0.45 * peak:
        return [g]
    sx = g.w / float(w_)
    left = Glyph((g.x0, g.y0, g.x0 + cut * sx, g.y1), g.mask[:, :cut].copy())
    right = Glyph((g.x0 + cut * sx, g.y0, g.x1, g.y1), g.mask[:, cut:].copy())
    if left.w <= 0 or right.w <= 0:
        return [g]
    return [left, right]

def date_glyphs(gs):
    """从一格里取出"日期本身"的字形：最大那层字里的最左边 1~2 个。"""
    if not gs:
        return []
    hmax = max(g.h for g in gs)
    big = [g for g in gs if g.h >= 0.75 * hmax]
    narrow = [g for g in big if g.w <= 0.85 * g.h]
    src = narrow or big
    src = sorted(src, key=lambda g: g.x0)
    if src and src[0].w > 0.95 * src[0].h:
        src = _split_pair(src[0]) + src[1:]
        src = sorted(src, key=lambda g: g.x0)
    return src[:MAX_DIGITS]

def _run_from(cells: dict, r0: int, c0: int, ndays: int) -> list[tuple[int, int]]:
    """从 (r0,c0) 开始，按七列往下数，数出连续的一段日期格。"""
    out = []
    for k in range(ndays):
        r = r0 + (c0 + k) // 7
        c = (c0 + k) % 7
        if (r, c) not in cells:
            break
        out.append((r, c))
    return out

def _fringe_start(cells: dict, ndays: int, prefer_c0: int | None,
                  expect_before: int | None = None,
                  expect_after: int | None = None,
                  light: dict | None = None):
    """首行左侧印上月尾、末行右侧印下月头的版式：1 号在哪一格。"""
    if prefer_c0 is None or light is None:
        return None
    rows = sorted({r for (r, _c) in cells})
    for r0 in rows[:2]:
        if (r0, prefer_c0) not in cells:
            continue
        if light.get((r0, prefer_c0)):
            continue
        run = _run_from(cells, r0, prefer_c0, ndays)
        if len(run) < ndays:
            continue
        last = run[-1]
        bf = [(r, c) for (r, c) in cells if r < r0 or (r == r0 and c < prefer_c0)]
        af = [(r, c) for (r, c) in cells if r > last[0] or (r == last[0] and c > last[1])]
        if expect_before is not None and len(bf) != expect_before:
            continue
        if expect_after is not None and len(af) != expect_after:
            continue
        if not (0 <= len(bf) <= 6 and 0 <= len(af) <= 6):
            continue
        if not (bf or af):
            continue
        if not all(light.get(k) for k in bf + af):
            continue
        return r0, prefer_c0, run
    return None

def assign_days(cells: dict, ndays: int, prefer_c0: int | None = None,
                light: dict | None = None):
    """找出"1 号"在哪一格，再顺着七列把整个月编上号。

    返回 (expects, r0, c0, covered)。
    """
    if not cells:
        return {}, 0, 0, 0

    fr = _fringe_start(cells, ndays, prefer_c0, light=light)
    if fr is not None:
        r0, c0, run = fr
        expects = _expects_from(cells, r0, c0, ndays, next_head=True)
        return expects, r0, c0, ndays

    by_row: dict[int, list[int]] = {}
    for (r, c) in cells:
        by_row.setdefault(r, []).append(c)
    rows = sorted(by_row)

    best = None
    for r0 in rows[:3]:
        for c0 in sorted(by_row[r0]):
            run = _run_from(cells, r0, c0, ndays)
            n = len(run)
            if n < max(7, ndays - 4):
                continue
            before = sum(1 for (r, c) in cells
                         if r < r0 or (r == r0 and c < c0))
            key = (-n, before, r0, c0)
            if best is None or key < best[0]:
                best = (key, r0, c0, run)

    if best is None:
        r0 = rows[0]
        c0 = min(by_row[r0])
        run = _run_from(cells, r0, c0, ndays)
        best = ((-len(run), 0, r0, c0), r0, c0, run)

    _key, r0, c0, run = best
    expects = _expects_from(cells, r0, c0, ndays)
    covered = len({d for d in expects.values() if d is not None and 1 <= d <= ndays})
    return expects, r0, c0, covered

def _expects_from(cells: dict, r0: int, c0: int, ndays: int, next_head: bool = False):
    """按"1 号在第 r0 行第 c0 列"这条位置公式，给每格写上它该是几号。"""
    expects: dict[tuple[int, int], int | None] = {}
    for (r, c) in cells:
        if r < r0 or (r == r0 and c < c0):
            expects[(r, c)] = PREV
            continue
        d = (r - r0) * 7 + (c - c0) + 1
        if 1 <= d <= ndays:
            expects[(r, c)] = d
        elif next_head:
            expects[(r, c)] = NEXT
        else:
            expects[(r, c)] = None
    return expects

def align_run(cells: dict, ndays: int, prefer_c0: int | None = None,
              light: dict | None = None):
    """剪掉混进网格的"多余一行"，让格子数和这个月的天数对得上。

    返回 (cells, r0, c0)，行号已压紧到 0 起。
    """
    if not cells:
        return cells, 0, 0

    fr = _fringe_start(cells, ndays, prefer_c0, light=light)
    if fr is not None:
        return dict(cells), fr[0], fr[1]

    rows = sorted({r for r, _c in cells})
    rmin_all, rmax_all = rows[0], rows[-1]

    best = None
    for top in (0, 1, 2):
        for bot in (0, 1):
            rmin = rmin_all + top
            rmax = rmax_all - bot
            if rmin > rmax:
                continue
            sub = {(r - rmin, c): v for (r, c), v in cells.items() if rmin <= r <= rmax}
            if len(sub) < max(7, ndays - 8):
                continue
            _e, _r0, _c0, covered = assign_days(sub, ndays)
            sc = (abs(len(sub) - ndays)
                  + (ndays - covered) * 3.0
                  + 0.01 * (top + bot))
            if best is None or sc < best[0] - 1e-9:
                best = (sc, sub)

    cs = best[1] if best else dict(cells)
    _e, r0, c0, _cov = assign_days(cs, ndays)
    return cs, r0, c0

def reading_order(cells: dict) -> list[tuple[int, int]]:
    """从左到右、从上到下 —— 台历的日期就是按这个顺序排的。"""
    return sorted(cells.keys(), key=lambda k: (k[0], k[1]))
