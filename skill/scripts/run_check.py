#!/usr/bin/env python3
"""台历校对引擎的 Agent 入口（仓库版）。

用法：
  python scripts/run_check.py 台历.pdf --year 2027 [--first-col auto|mon|sun]
                                [--start-month 1] [-o 输出目录] [--png] [--diag]

引擎位置自动找，两种布局都能跑：
  · Skill 布局：<ROOT>/engine/calproof/   （把仓库根 calproof/ 复制进 skill/engine/）
  · 仓库布局：<仓库根>/calproof/          （直接在本仓库里跑）

产物（默认输出目录 = 稿件同级的 <文件名>_校对/）：
  result.json   结构化结果（给 Agent 读，也给人看）
  report.html   可视化报告（红框标错，可直接交付给用户）
  pages/        加 --png 时：每页原图 + 标注图（失败诊断用）
  cells.csv     加 --diag 时：每格明细（行列、读数、应为、置信度）

stdout 打印：一行 JSON 摘要 + 人话结论。Agent 以 result.json 为准。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # .../<skill>/scripts
SKILL_ROOT = os.path.dirname(HERE)                    # .../<skill>


def _locate_engine():
    """返回 (要加到 sys.path 的目录, calproof 包目录, 工作根目录)。"""
    cands = [
        (os.path.join(SKILL_ROOT, "engine"), SKILL_ROOT),                 # Skill 布局
        (os.path.dirname(SKILL_ROOT), os.path.dirname(SKILL_ROOT)),       # 仓库布局
        (SKILL_ROOT, SKILL_ROOT),
    ]
    for path_dir, root in cands:
        pkg = os.path.join(path_dir, "calproof")
        if os.path.isdir(pkg) and os.path.isfile(os.path.join(pkg, "pipeline.py")):
            return path_dir, pkg, root
    raise SystemExit("找不到 calproof 引擎包：请把仓库根的 calproof/ 放在 <skill>/engine/ "
                     "或与 scripts/ 同级上两层的仓库根里。")


ENGINE_PATH, PKG_DIR, WORK_ROOT = _locate_engine()
sys.path.insert(0, ENGINE_PATH)

# 版式档案：跟着工作根走（Skill 用 <skill>/profiles/，仓库用 <仓库根>/profiles/）。
# 两个位置用 scripts/sync_engine.ps1 双向合并。
os.environ.setdefault("CALPROOF_PROFILES", os.path.join(WORK_ROOT, "profiles"))

from calproof.pipeline import run                      # noqa: E402
from calproof.report import build_report, _annotate    # noqa: E402


def engine_fingerprint() -> str:
    """引擎指纹：本地工具与 Skill 里的代码是不是同一份，一比就知道。"""
    h = hashlib.sha1()
    for fn in sorted(os.listdir(PKG_DIR)):
        if fn.endswith(".py"):
            with open(os.path.join(PKG_DIR, fn), "rb") as f:
                h.update(fn.encode() + f.read())
    return h.hexdigest()[:12]


def build_health(res: dict, ndays_map: dict) -> dict:
    """体检：工具自己判断这次读懂了没有。超线就建议交给 AI。"""
    pages = res["pages"]
    ok = [p for p in pages if not p.get("skip")]
    skip = [p for p in pages if p.get("skip")]

    scores = []
    suspect = 0
    bad_days = []
    low_conf = 0
    for p in ok:
        for v in (p.get("cells") or {}).values():
            if v.get("value") is not None:
                s = float(v.get("score") or 0)
                scores.append(s)
                if s < 0.25:
                    low_conf += 1
            if v.get("expect") is not None and v.get("expect") != -1 and v.get("value") is None:
                suspect += 1
        days = ndays_map.get(p["month"])
        if days and len(p.get("cells") or {}) < days * 0.85:
            bad_days.append({"month": p["month"], "cells": len(p.get("cells") or {}), "days": days})

    min_conf = round(min(scores), 3) if scores else 0.0
    need_ai, reasons = False, []
    total = len(pages)
    # 封面/封底被跳过是正常的；只有"夹在中间的月历页"被跳过才说明引擎没读懂
    mid_skip = [p["page"] for p in skip if 1 < p["page"] < total]
    if len(ok) == 0:
        need_ai = True
        reasons.append("整份文件没有一页能认出月历（全部跳过）")
    elif mid_skip:
        need_ai = True
        reasons.append("第 %s 页夹在月历页中间却读不出，需要确认是真封面还是引擎盲区"
                       % "、".join(str(x) for x in mid_skip))
    if bad_days:
        need_ai = True
        reasons.append("有 %d 个月的格子明显少于当月天数" % len(bad_days))
    if scores and min_conf < 0.25 and low_conf >= 3 and low_conf >= 0.1 * len(scores):
        # 一两格拿不准只算"待确认"（报告里已经标出来了）；成片拿不准才说明分组糊了
        need_ai = True
        reasons.append("有 %d 格读数置信度很低（最低 %.2f），形状分组可能糊了"
                       % (low_conf, min_conf))
    if res.get("fatal"):
        need_ai = True
        reasons.insert(0, res["fatal"])

    return {
        "pages_total": total,
        "pages_read": len(ok),
        "pages_skipped": len(skip),
        "skipped_pages": [{"page": p["page"], "reason": p.get("reason", "")} for p in skip],
        "glyphs": res.get("glyphs"),
        "shape_groups": res.get("groups"),
        "min_conf": min_conf,
        "expected_but_empty": suspect,
        "thin_months": bad_days,
        "need_ai": need_ai,
        "reasons": reasons,
    }


def dump_cells(res: dict, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["页", "月", "行", "列", "读到的字", "读数", "应为", "位置正确", "置信度"])
        for p in res["pages"]:
            if p.get("skip"):
                continue
            for (r, c), v in sorted((p.get("cells") or {}).items()):
                w.writerow([p["page"], p["month"], r, c, v.get("text"), v.get("value"),
                            v.get("expect"), v.get("pos_ok"), round(float(v.get("score") or 0), 3)])


def dump_pngs(res: dict, outdir: str) -> list:
    from PIL import Image
    pdir = os.path.join(outdir, "pages")
    os.makedirs(pdir, exist_ok=True)
    made = []
    for p in res["pages"]:
        fn = os.path.join(pdir, "p%02d_原页.png" % p["page"])
        Image.fromarray(p["image"]).save(fn)
        made.append(fn)
        if not p.get("skip"):
            fn2 = os.path.join(pdir, "p%02d_标注.png" % p["page"])
            import base64
            with open(fn2, "wb") as f:
                f.write(base64.b64decode(_annotate(p)))
            made.append(fn2)
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_check")
    ap.add_argument("pdf")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--first-col", default="auto", choices=["auto", "mon", "sun"])
    ap.add_argument("--start-month", type=int, default=1)
    ap.add_argument("-o", "--out", default=None, help="输出目录")
    ap.add_argument("--png", action="store_true", help="导出每页图片（诊断用）")
    ap.add_argument("--diag", action="store_true", help="导出每格明细 CSV")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.pdf):
        print(json.dumps({"ok": False, "error": "找不到文件：%s" % a.pdf}, ensure_ascii=False))
        return 2

    outdir = a.out or os.path.join(
        os.path.dirname(os.path.abspath(a.pdf)),
        os.path.splitext(os.path.basename(a.pdf))[0] + "_校对")
    os.makedirs(outdir, exist_ok=True)

    quiet = []
    src = a.pdf
    tmp_pdf = None
    if os.path.splitext(a.pdf)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
        # 图片：包成单页 PDF，再走同一套流程（位图页会走像素级切字）
        from calproof.cli import _image_to_pdf
        src = tmp_pdf = _image_to_pdf(a.pdf, a.year)
    res = run(src, a.year, a.first_col, a.start_month, None,
              progress=lambda m: quiet.append(m))
    if tmp_pdf:
        try:
            os.remove(tmp_pdf)
        except OSError:
            pass

    from calproof.truth import month_truth
    from calproof.layers import date_glyphs
    ndays = {p["month"]: month_truth(a.year, p["month"])["days"]
             for p in res["pages"] if not p.get("skip")}

    html = os.path.join(outdir, "report.html")
    build_report(res, html)

    n_glyph = 0
    for p in res["pages"]:
        if p.get("skip"):
            continue
        for v in (p.get("structure") or {}).get("cells", {}).values():
            n_glyph += len(date_glyphs(v))

    health = build_health(res, ndays)
    health["glyphs"] = n_glyph

    pages_out = []
    for p in res["pages"]:
        rec = {"page": p["page"], "skip": bool(p.get("skip"))}
        if p.get("skip"):
            rec["reason"] = p.get("reason", "")
            pages_out.append(rec)
            continue
        cells = p.get("cells") or {}
        rec.update({
            "month": p["month"],
            "n_error": p["n_error"],
            "n_warn": p["n_warn"],
            "cells": len(cells),
            "days": ndays.get(p["month"]),
            "rows": len({r for (r, _c) in cells}),
            "cols": len({c for (_r, c) in cells}),
            "findings": [{"severity": f["severity"], "kind": f["kind"], "msg": f["msg"]}
                         for f in p["findings"]],
        })
        pages_out.append(rec)

    payload = {
        "ok": True,
        "engine": engine_fingerprint(),
        "engine_dir": PKG_DIR,
        "pdf": os.path.abspath(a.pdf),
        "year": a.year,
        "first_col": res.get("first_col") or res.get("anchor"),
        "checked_months": res.get("checked", 0),
        "n_error": res.get("n_error", 0),
        "n_warn": res.get("n_warn", 0),
        "health": health,
        "pages": pages_out,
        "report_html": html,
        "outdir": outdir,
    }
    if a.diag:
        payload["cells_csv"] = os.path.join(outdir, "cells.csv")
        dump_cells(res, payload["cells_csv"])
    if a.png:
        payload["pngs"] = dump_pngs(res, outdir)

    with open(os.path.join(outdir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ---- 人话输出 ----
    print(json.dumps({"ok": True, "n_error": payload["n_error"], "n_warn": payload["n_warn"],
                      "checked": payload["checked_months"], "need_ai": health["need_ai"],
                      "report_html": html, "result_json": os.path.join(outdir, "result.json"),
                      "engine": payload["engine"]}, ensure_ascii=False))
    print()
    print("=" * 46)
    print("已校对 %d 个月：%d 个错误，%d 处待确认"
          % (payload["checked_months"], payload["n_error"], payload["n_warn"]))
    for p in pages_out:
        if p.get("skip"):
            print("  第 %d 页 跳过：%s" % (p["page"], p.get("reason")))
            continue
        if p["n_error"]:
            print("  ✕ %d年%d月（第%d页）" % (a.year, p["month"], p["page"]))
            for f in p["findings"]:
                if f["severity"] == "error":
                    print("      - " + f["msg"])
    print("=" * 46)
    if health["need_ai"]:
        print("体检：这次没完全读懂 —— " + "；".join(health["reasons"]))
        print("→ 建议进入诊断流程（见 references/诊断手册.md）")
    else:
        print("体检：正常（最低置信度 %.2f，%d 页全部识别）"
              % (health["min_conf"], health["pages_read"]))
    print("报告：" + html)
    return 0 if payload["n_error"] == 0 and not health["need_ai"] else 1


if __name__ == "__main__":
    sys.exit(main())
