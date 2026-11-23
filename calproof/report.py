"""生成校对报告（单个 HTML 文件，可以直接发给客户或存档）。"""
from __future__ import annotations

import base64
import io
import os

import numpy as np
from PIL import Image, ImageDraw

RED = (229, 72, 77)
AMBER = (245, 165, 36)


def _annotate(page: dict, max_w: int = 1000) -> str:
    """在原页面上画红框，返回 base64 PNG。"""
    arr = page["image"]
    im = Image.fromarray(arr)
    zoom = page["zoom"]

    ox = oy = 0
    cells = page.get("cells") or {}
    boxes = page.get("grid_boxes") or {}
    if cells:
        allb = [c["bbox"] for c in cells.values()] + list(boxes.values())
        x0 = min(b[0] for b in allb)
        y0 = min(b[1] for b in allb)
        x1 = max(b[2] for b in allb)
        y1 = max(b[3] for b in allb)
        rows_used = sorted({r for (r, _c) in cells})
        cols_used = sorted({c for (_r, c) in cells})
        pitch_y = (y1 - y0) / max(1, len(rows_used) - 1)
        pitch_x = (x1 - x0) / max(1, len(cols_used) - 1)
        mx = pitch_x * 0.62
        cy0 = max(0, int((y0 - 1.55 * pitch_y) * zoom))
        cy1 = min(im.height, int((y1 + 0.45 * pitch_y) * zoom))
        cx0 = max(0, int((x0 - mx) * zoom))
        cx1 = min(im.width, int((x1 + mx) * zoom))
        if cx1 - cx0 > 80 and cy1 - cy0 > 80:
            im = im.crop((cx0, cy0, cx1, cy1))
            ox, oy = cx0, cy0

    draw = ImageDraw.Draw(im)
    lw = max(2, int(im.width / 420))
    hits = {}
    for f in page["findings"]:
        key = f.get("cell")
        if key is None:
            continue
        sev = f["severity"]
        if sev not in ("error", "warn"):
            continue
        cur = hits.get(key)
        if cur != "error":
            hits[key] = "error" if sev == "error" else "warn"
    for key, sev in hits.items():
        if key in cells:
            bx0, by0, bx1, by1 = cells[key]["bbox"]
        elif key in boxes:
            bx0, by0, bx1, by1 = boxes[key]
        else:
            continue
        pad = 3 * zoom
        box = [bx0 * zoom - pad - ox, by0 * zoom - pad - oy,
               bx1 * zoom + pad - ox, by1 * zoom + pad - oy]
        color = RED if sev == "error" else AMBER
        draw.rectangle(box, outline=color, width=lw)

    if im.width > max_w:
        ratio = max_w / im.width
        im = im.resize((max_w, int(im.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


CSS = """
:root{--ink:#1a1a1a;--muted:#6b6b6b;--line:#e4e2dd;--bg:#faf9f7;
--red:#e5484d;--amber:#c77a00;--green:#2f9e63}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:40px 28px 80px}
h1{font-size:26px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin-bottom:28px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:36px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 22px;min-width:150px}
.card .n{font-size:30px;font-weight:600;line-height:1.2}
.card .k{font-size:12.5px;color:var(--muted);letter-spacing:.02em}
.card.bad .n{color:var(--red)}.card.warn .n{color:var(--amber)}
.card.good .n{color:var(--green)}
.month{background:#fff;border:1px solid var(--line);border-radius:14px;
margin-bottom:26px;overflow:hidden}
.mhead{display:flex;align-items:center;gap:12px;padding:14px 22px;border-bottom:1px solid var(--line)}
.mhead h2{font-size:17px;font-weight:600;margin:0}
.tag{font-size:12px;padding:2px 9px;border-radius:20px;font-weight:500}
.tag.ok{background:#e8f6ee;color:var(--green)}
.tag.bad{background:#fdecec;color:var(--red)}
.tag.warn{background:#fdf3e2;color:var(--amber)}
.tag.skip{background:#efeeec;color:var(--muted)}
.body{padding:18px 22px}
img.page{width:100%;border:1px solid var(--line);border-radius:8px;display:block}
ul.find{margin:16px 0 0;padding:0;list-style:none}
ul.find li{padding:9px 0 9px 26px;border-top:1px solid var(--line);position:relative;font-size:14.2px}
ul.find li:before{position:absolute;left:0;top:9px;font-weight:600}
li.err:before{content:"✕";color:var(--red)}
li.warn:before{content:"!";color:var(--amber)}
li.info:before{content:"i";color:var(--muted)}
.note{color:var(--muted);font-size:13px}
.legend{font-size:13px;color:var(--muted);margin-top:10px}
.banner{background:#fdf3e2;border:1px solid #f3ddb5;border-radius:10px;
padding:14px 18px;margin-top:14px;font-size:14px}
.banner b{display:block;margin-bottom:4px}
.foot{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
code{background:#f1efec;padding:1px 5px;border-radius:4px;font-size:13px}
"""


def build_report(result: dict, out_path: str) -> str:
    pages = result["pages"]
    total = result["n_error"]
    warns = result["n_warn"]
    checked = result["checked"]
    skipped = [p for p in pages if p.get("skip")]
    anchor = result.get("anchor", result.get("first_col", "mon"))
    anchor_cn = "周日" if anchor == "sun" else "周一"

    if total == 0 and warns == 0:
        head = '<span class="tag ok">全部通过</span>'
    elif total == 0:
        head = '<span class="tag warn">无硬错误，有待确认</span>'
    else:
        head = '<span class="tag bad">发现错误</span>'

    parts = []
    parts.append(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台历日期校对报告 · {result['year']}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>台历日期校对报告 {head}</h1>
<div class="sub">{os.path.basename(result['pdf'])} · 校对年份 {result['year']} 年 ·
版面以「{anchor_cn}」为第一列（自动识别）· 共 {result.get('groups', '-')} 种字形</div>
<div class="cards">
  <div class="card {'bad' if total else 'good'}"><div class="n">{total}</div><div class="k">确定错误</div></div>
  <div class="card warn"><div class="n">{warns}</div><div class="k">待人工确认</div></div>
  <div class="card"><div class="n">{checked}</div><div class="k">已校对月份</div></div>
  <div class="card"><div class="n">{len(skipped)}</div><div class="k">跳过的页</div></div>
</div>""")

    for p in pages:
        if p.get("skip"):
            parts.append(f"""<div class="month"><div class="mhead">
<h2>第 {p['page']} 页</h2><span class="tag skip">跳过</span></div>
<div class="body"><div class="note">{p.get('reason', '')}</div></div></div>""")
            continue

        errs = [f for f in p["findings"] if f["severity"] == "error"]
        wrns = [f for f in p["findings"] if f["severity"] == "warn"]
        infos = [f for f in p["findings"] if f["severity"] == "info"]

        if errs:
            tag = '<span class="tag bad">发现错误</span>'
        elif wrns:
            tag = '<span class="tag warn">待确认</span>'
        else:
            tag = '<span class="tag ok">通过</span>'

        b64 = _annotate(p)
        rows = []
        for f in errs + wrns + infos:
            cls = "err" if f["severity"] == "error" else ("warn" if f["severity"] == "warn" else "info")
            note = f" <span class='note'>{f['note']}</span>" if f.get("note") else ""
            rows.append(f'<li class="{cls}">{f["msg"]}{note}</li>')

        body_inner = ('<ul class="find">' + "".join(rows) + "</ul>") if rows else \
            '<div class="legend">这一页没有发现问题。</div>'

        parts.append(f"""<div class="month"><div class="mhead">
<h2>{result['year']} 年 {p['month']} 月</h2>{tag}
<span class="note">第 {p['page']} 页 · 读到 {len(p['cells'])} 个日期 · 分组纯度 {p.get('mean_score', '-')}</span></div>
<div class="body"><img class="page" src="data:image/png;base64,{b64}" alt="第{p['page']}页标注">
{body_inner}
</div></div>""")

    mean_score = round(sum(p.get('mean_score', 0) for p in pages if not p.get('skip')) / max(checked, 1), 3)
    parts.append(f"""<div class="foot">红框＝确定错误，橙框＝机器没认准、请你看一眼。
数字是靠"形状分组"读出来的：同一个数字在文件里形状完全一样，所以能归成一组；
每组叫什么名字，由"这一格该是几号"投票决定，再用真实历法核对星期与天数。
全程不需要字体文件，也不需要联网。<br>
本次共归出 {result.get('groups', '-')} 种字形，平均分组纯度
{mean_score}。纯度越接近 1，读数越可靠。</div></div></body></html>""")

    html = "".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
