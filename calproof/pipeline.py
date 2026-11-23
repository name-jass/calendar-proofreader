"""整条流水线：PDF → 每页网格 → 形状分组 → 按位置反推数字 → 对账 → 结果。

关键点：全程不需要字体文件，也不需要 OCR 模型。
同一个数字在文件里形状完全一样，靠"分组"就能认；每组叫什么名字，
由"这一格该是几号"投票决定 —— 历法本身就是字典。
"""
from __future__ import annotations

import os

try:
    import pymupdf as fitz
except ImportError:
    import fitz  # type: ignore

import numpy as np

from .check import anchor_mismatch, build_truth, verify_page
from .cluster import cluster_shapes, label_by_position
from .extract import PageImage, collect_items
from .layers import NEXT, PREV, align_run, assign_days, date_glyphs, page_structure, _fringe_start
from .linear import page_linear
from .truth import month_truth

ANCHORS = ("sun", "mon")      # 第一列是周日 / 周一，两种版式都试

def _cell_bbox(glyphs):
    return (
        min(g.x0 for g in glyphs), min(g.y0 for g in glyphs),
        max(g.x1 for g in glyphs), max(g.y1 for g in glyphs),
    )

def _grid_boxes(cells: dict) -> dict:
    """把"没有字的空格"的位置也补出来（漏排的那一格位置上本来什么都没有）。"""
    if not cells:
        return {}
    cx, cy, ws, hs = {}, {}, [], []
    for (r, c), info in cells.items():
        b = info["bbox"]
        cx.setdefault(c, []).append((b[0] + b[2]) / 2.0)
        cy.setdefault(r, []).append((b[1] + b[3]) / 2.0)
        ws.append(b[2] - b[0])
        hs.append(b[3] - b[1])
    cxs = {c: float(np.median(v)) for c, v in cx.items()}
    cys = {r: float(np.median(v)) for r, v in cy.items()}
    w = float(np.median(ws))
    h = float(np.median(hs))
    out = {}
    for r in cys:
        for c in cxs:
            out[(r, c)] = (cxs[c] - w / 2.0, cys[r] - h / 2.0,
                           cxs[c] + w / 2.0, cys[r] + h / 2.0)
    return out

def run(pdf_path: str, year: int, first_col: str = "auto", start_month: int = 1,
        font_dir: str | None = None, progress=None) -> dict:
    def say(msg):
        if progress:
            progress(msg)

    doc = fitz.open(pdf_path)
    pages: list[dict] = []
    month = start_month

    # ---------- 1. 逐页找日期网格 ----------
    for pno in range(len(doc)):
        if month > 12:
            pages.append({"page": pno + 1, "skip": True,
                          "reason": "已经排满 12 个月，这一页按封面 / 附页跳过"})
            continue
        page = doc[pno]
        page.set_rotation(0)
        try:
            items = collect_items(page)
        except Exception as e:
            pages.append({"page": pno + 1, "skip": True, "reason": f"解析失败：{e}"})
            continue

        img = PageImage(page)
        ndays = month_truth(year, month)["days"]
        glyphs = None
        if not items:
            # 没有文字层、也没有矢量字形 —— 这一页是位图（图片），
            # 直接从像素里切字，后面认形状、投票命名、对历法完全通用
            from .raster import glyphs_from_bitmap
            glyphs = glyphs_from_bitmap(img.img, scale=img.zoom)
        st = page_linear(img, items, page.rect.height, ndays, glyphs=glyphs)
        if not st["ok"]:
            st = page_structure(img, items, page.rect.height, page, glyphs=glyphs)
        learned = None
        provisional = False
        if not st["ok"]:
            from . import profile as _pf
            prof = _pf.match(_pf.load_all(), page.rect.width, page.rect.height)
            if prof:
                st = _pf.read_by_profile(img, items, prof, ndays, glyphs=glyphs)
                if st["ok"]:
                    provisional = True
                    say(f"第 {pno + 1} 页：套用档案「{prof.get('name','')}」")
            if not st["ok"]:
                st = _pf.learn_flow(img, items, page.rect.width, page.rect.height, ndays,
                                    glyphs=glyphs)
                if st["ok"]:
                    provisional = True
                    learned = st.get("learned_profile")
                    learned["source"] = os.path.basename(pdf_path)
                    learned["source_page"] = pno + 1
        rec = {"page": pno + 1, "img": img, "image": img.img, "zoom": img.zoom}
        if not st["ok"]:
            reason = st["reason"]
            if not items:
                try:
                    if page.get_images():
                        reason = ("这一页的文字是位图（图片），不是矢量路径，"
                                  "跟转曲矢量不是一回事，需要用图像识别，路子不同")
                except Exception:
                    pass
            rec.update({"skip": True, "reason": reason})
            pages.append(rec)
            continue

        rec.update({"skip": False, "month": month, "structure": st})
        rec["provisional"] = provisional
        if learned:
            rec["learned_profile"] = learned
        month += 1
        pages.append(rec)
        if st.get("kind") == "linear":
            say(f"第 {pno + 1} 页：横排版式，{st['rows_n']} 行共 {len(st['cells'])} 个日期槽，"
                f"{st['alphabet']} 种字形")
        else:
            say(f"第 {pno + 1} 页：找到 {len(st['cells'])} 个日期格，"
                f"{st['alphabet']} 种字形")

    ok = [p for p in pages if not p.get("skip")]
    if not ok:
        doc.close()
        return {"pdf": pdf_path, "year": year, "anchor": first_col, "first_col": first_col,
                "pages": pages, "checked": 0, "blocked": 0, "n_error": 0, "n_warn": 0,
                "groups": 0, "fatal": "整份文件里没找到可以校对的月历页"}

    # ---------- 2. 全库一起按形状分组 ----------
    allg = [g for p in ok for v in p["structure"]["cells"].values() for g in date_glyphs(v)]
    labels, _cents, _sims, _norms = cluster_shapes([g.mask for g in allg])
    for g, cid in zip(allg, labels):
        g.cid = cid
    n_groups = len(set(labels))
    say(f"全本共 {len(allg)} 个字形，归成 {n_groups} 种形状")

    # ---------- 2.5 先用"日历列"把锚点定下来（只看几何和字色，不看读数）----------
    def cell_light(page_rec, key, glyphs) -> bool:
        """这一格的日期字是不是浅灰（= 别月的日子）。"""
        try:
            img = page_rec["image"]
            zoom = page_rec["zoom"]
            if not glyphs:
                return False
            x0 = min(g.x0 for g in glyphs)
            y0 = min(g.y0 for g in glyphs)
            x1 = max(g.x1 for g in glyphs)
            y1 = max(g.y1 for g in glyphs)
            px0 = max(0, int(x0 * zoom))
            py0 = max(0, int(y0 * zoom))
            px1 = min(img.shape[1], int(np.ceil(x1 * zoom)))
            py1 = min(img.shape[0], int(np.ceil(y1 * zoom)))
            if px1 - px0 < 2 or py1 - py0 < 2:
                return False
            crop = img[py0:py1, px0:px1].astype(np.float32)
            lum = crop[..., 0] * 0.299 + crop[..., 1] * 0.587 + crop[..., 2] * 0.114
            return float(np.percentile(lum, 5)) > 150.0
        except Exception:
            return False

    for p in ok:
        if p["structure"].get("kind") == "linear":
            continue
        cells = p["structure"]["cells"]
        light = {}
        for k, gs in cells.items():
            light[k] = cell_light(p, k, date_glyphs(gs))
        p["light"] = light

    def fringe_hits(anchor: str) -> int:
        hits = 0
        for p in ok:
            if p["structure"].get("kind") == "linear":
                continue
            nd = month_truth(year, p["month"])["days"]
            t = month_truth(year, p["month"], anchor)
            off = t["offset"]
            after_want = 7 * t["weeks"] - off - nd
            fr = _fringe_start(p["structure"]["cells"], nd, off,
                               expect_before=off, expect_after=after_want,
                               light=p.get("light"))
            if fr is not None:
                hits += 1
        return hits

    prefer_pre = first_col if first_col in ANCHORS else None
    hits = {a: fringe_hits(a) for a in ANCHORS}
    anchor_pre = None
    if max(hits.values()):
        top = [a for a in ANCHORS if hits[a] == max(hits.values())]
        anchor_pre = prefer_pre if prefer_pre in top else top[0]
        say("版式识别：首行印上月尾、末行印下月头，按「"
            + ("周日" if anchor_pre == "sun" else "周一") + "」开头定位 1 号")

    # ---------- 3. 按阅读顺序推算每格"应该是几号"，投票给每组取名字 ----------
    for p in ok:
        ndays = month_truth(year, p["month"])["days"]
        if p["structure"].get("kind") == "linear":
            cells = p["structure"]["cells"]
            idx = p["structure"]["index"]
            keys = sorted(cells, key=lambda k: idx[k])
            p["expects"] = {k: (idx[k] + 1 if idx[k] + 1 <= ndays else None) for k in keys}
            p["sequence"] = [(idx[k] + 1, date_glyphs(cells[k])) for k in keys
                             if idx[k] + 1 <= ndays]
            p["r0"], p["c0"] = 0, 0
            continue
        before = len(p["structure"]["cells"])
        pre_c0 = None
        if anchor_pre is not None:
            pre_c0 = month_truth(year, p["month"], anchor_pre)["offset"]
        cells, r0, c0 = align_run(p["structure"]["cells"], ndays, prefer_c0=pre_c0,
                                  light=p.get("light"))
        p["structure"]["cells"] = cells
        if len(cells) != before:
            say(f"第 {p['page']} 页：剪掉 {before - len(cells)} 个网格外的装饰字形")
        expects, r0, c0, _covered = assign_days(cells, ndays, prefer_c0=pre_c0,
                                                light=p.get("light"))
        seq = []
        run = 0
        for (r, c) in sorted(cells, key=lambda k: (k[0], k[1])):
            e = expects[(r, c)]
            if e in (PREV, NEXT):
                continue               # 上月末的灰色日期不参与投票
            d = e if e is not None else run + 1
            run = max(run, d)
            seq.append((d, date_glyphs(cells[(r, c)])))
        p["r0"], p["c0"], p["sequence"] = r0, c0, seq
        p["expects"] = expects

    table, margin, _votes = label_by_position(ok, n_groups, _cents)
    unknown = [cid for cid, ch in table.items() if ch == "?"]
    if unknown:
        say(f"有 {len(unknown)} 组字形没能定名（可能是装饰元素），这些格子会标为待确认")

    # ---------- 4. 读出每一格 ----------
    for p in ok:
        out = {}
        expects = p.get("expects", {})
        slot_boxes = p["structure"].get("boxes") or {}
        for (r, c), gs in p["structure"]["cells"].items():
            dgs = date_glyphs(gs)          # 格里的日期只取最左边 1~2 个字形
            text = "".join(table.get(g.cid, "?") for g in dgs)
            value = int(text) if text.isdigit() else None
            if value is not None and value > 31 and len(dgs) == 2:
                # 第二位其实是农历/装饰，只有第一位是日期
                text = table.get(dgs[0].cid, "?")
                value = int(text) if text.isdigit() else None
            score = min((margin.get(g.cid, 0.0) for g in dgs), default=1.0)
            exp = expects.get((r, c))
            bbox = _cell_bbox(dgs) if dgs else slot_boxes.get((r, c), (0, 0, 0, 0))
            out[(r, c)] = {"glyphs": gs, "digits": dgs, "text": text, "value": value,
                           "score": score, "bbox": bbox,
                           "expect": exp,
                           "pos_ok": exp is not None and exp not in (PREV, NEXT)}
        p["cells"] = out
        p["grid_boxes"] = slot_boxes if slot_boxes else _grid_boxes(out)

    # ---------- 4.5 学过 / 套档案的页必须自证 ----------
    # 参数搜索有可能"槽数凑对了、位置切错了"，那种页会报出一堆假错误。
    # 要求它的读数必须和历法对得上，对不上就宁可跳过，也不能把假错误丢给用户。
    rejected = []
    for p in ok:
        if not p.get("provisional"):
            continue
        nd = month_truth(year, p["month"])["days"]
        mism = sum(1 for c in p["cells"].values()
                   if c["expect"] is not None and c["expect"] not in (PREV, NEXT)
                   and c["value"] != c["expect"])
        if mism > max(3, int(0.12 * nd)):
            p["skip"] = True
            p["reason"] = ("这一页的版式引擎没吃准（靠猜切出来的位置和历法对不上），"
                           "这次跳过，不当成错误")
            rejected.append(p["page"])
        elif p.get("learned_profile") is not None:
            rec_ok = p
    if rejected:
        say(f"第 {'、'.join(str(x) for x in rejected)} 页：切法对不上历法，已按跳过处理（不算错）")
    ok = [p for p in ok if not p.get("skip")]

    # 自证通过的"新学的页"才存档案：同一款取各行最大容量，一个档案覆盖全年
    learned_pages = [p for p in ok if p.get("learned_profile")]
    if learned_pages:
        from . import profile as _pf2
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        # 以"天数最多的那一页"为准（31 天那页第一行是满的，行容量最可信）
        auth = max(learned_pages, key=lambda p: month_truth(year, p["month"])["days"])
        prof = dict(auth["learned_profile"])
        prof["name"] = f"{base}-{prof['page_w']:.0f}x{prof['page_h']:.0f}"
        prof["rows"] = [dict(r) for r in prof["rows"]]
        try:
            _pf2.save(prof)
            say(f"新样式已存成档案「{prof['name']}」，下次同款台历会直接套用")
        except Exception as e:
            say(f"档案保存失败（不影响本次校对）：{e}")

    # ---------- 5. 自动判断"第一列是周几" ----------
    def total_bad(anchor: str) -> int:
        n = 0
        for p in ok:
            if p["structure"].get("kind") == "linear":
                continue            # 横排版式没有"列"
            truth = build_truth(year, p["month"], anchor)
            n += anchor_mismatch(p["cells"], truth, p["r0"], p["c0"], anchor)
        return n

    cand = {a: total_bad(a) for a in ANCHORS}
    prefer = first_col if first_col in ANCHORS else None
    anchor = min(ANCHORS, key=lambda a: (cand[a], 0 if a == prefer else 1))
    if prefer and anchor != prefer:
        say(f"检测到版面以「{'周日' if anchor == 'sun' else '周一'}」开头，"
            f"已按实际版式校对（原设置为「{'周日' if prefer == 'sun' else '周一'}」）")

    # ---------- 6. 对账 ----------
    for p in ok:
        truth = build_truth(year, p["month"], anchor)
        cols = p["structure"].get("cols")
        cols_n = len(cols) if isinstance(cols, list) else 7
        res = verify_page(p["page"], p["month"], p["cells"], truth,
                          p["r0"], p["c0"], anchor,
                          layout=p["structure"].get("kind", "grid"),
                          cols=cols_n)
        scores = [c["score"] for c in p["cells"].values() if c["value"] is not None]
        res.update({
            "skip": False,
            "blocked": False,
            "font": f"{n_groups} 种字形",
            "mean_score": round(float(sum(scores) / len(scores)), 3) if scores else 0.0,
            "pages": p.get("page"),
            "page_px": p["image"].shape[1],
            "page_py": p["image"].shape[0],
            "image": p["image"],
            "zoom": p["zoom"],
            "groups": n_groups,
        })
        p.update(res)
        say(f"第 {p['page']} 页 → {year}年{p['month']}月："
            f"{res['n_error']} 个错误，{res['n_warn']} 处待确认")

    doc.close()

    months = [p for p in pages if not p.get("skip")]
    return {
        "pdf": pdf_path,
        "year": year,
        "anchor": anchor,
        "first_col": anchor,
        "pages": pages,
        "checked": len(months),
        "blocked": 0,
        "n_error": sum(p["n_error"] for p in months),
        "n_warn": sum(p["n_warn"] for p in months),
        "groups": n_groups,
    }
