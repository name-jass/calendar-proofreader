"""生成一份“已转曲”的台历测试 PDF（不包含任何客户信息）。

做法和 Illustrator「创建轮廓」一样：把字体轮廓直接写成 PDF 路径指令，
文件里没有任何文字层，只有矢量图形 —— 和真正要处理的稿子同构。

故意埋了三个错，用来验证校对引擎能不能抓出来：
  3 月：17 日印成了 11 日
  7 月：整月往右错了一列
  11 月：漏掉了 23 日

依赖：pymupdf、fontTools（仅生成测试稿时需要）
用法：python tools/make_sample.py samples/台历2027_测试.pdf
"""
from __future__ import annotations

import calendar
import datetime
import os
import sys

import pymupdf as fitz
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

W, H = 595.0, 480.0
X0 = 60.0
PITCH = 68.0
YTOP = 396.0
ROWH = 54.0
NUM_SIZE = 23.0
SMALL_SIZE = 7.2
FONT_DIGIT = "C:/Windows/Fonts/bahnschrift.ttf"   # 不在内置字库里
FONT_CN = "C:/Windows/Fonts/msyh.ttc"

class OpsPen(BasePen):
    def __init__(self, glyphSet, scale, ox, oy):
        super().__init__(glyphSet)
        self.ops: list[str] = []
        self.s = scale
        self.ox = ox
        self.oy = oy
        self.cur = (0.0, 0.0)

    def _t(self, p):
        return (self.ox + p[0] * self.s, self.oy + p[1] * self.s)

    def _moveTo(self, pt):
        self.cur = pt
        x, y = self._t(pt)
        self.ops.append(f"{x:.3f} {y:.3f} m")

    def _lineTo(self, pt):
        self.cur = pt
        x, y = self._t(pt)
        self.ops.append(f"{x:.3f} {y:.3f} l")

    def _curveToOne(self, p1, p2, p3):
        a, b, c = self._t(p1), self._t(p2), self._t(p3)
        self.ops.append(
            f"{a[0]:.3f} {a[1]:.3f} {b[0]:.3f} {b[1]:.3f} {c[0]:.3f} {c[1]:.3f} c")
        self.cur = p3

    def _qCurveToOne(self, p1, p2):
        cur = self.cur
        c1 = (cur[0] + 2.0 / 3.0 * (p1[0] - cur[0]), cur[1] + 2.0 / 3.0 * (p1[1] - cur[1]))
        c2 = (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1]))
        a, b, c = self._t(c1), self._t(c2), self._t(p2)
        self.ops.append(
            f"{a[0]:.3f} {a[1]:.3f} {b[0]:.3f} {b[1]:.3f} {c[0]:.3f} {c[1]:.3f} c")
        self.cur = p2

    def _closePath(self):
        self.ops.append("h")

    def _endPath(self):
        self.ops.append("h")

class Face:
    def __init__(self, path, index=0):
        self.font = TTFont(path, fontNumber=index)
        self.cmap = self.font.getBestCmap()
        self.gs = self.font.getGlyphSet()
        self.hmtx = self.font["hmtx"]
        self.upem = self.font["head"].unitsPerEm

    def advance(self, text, size):
        s = size / self.upem
        w = 0.0
        for ch in text:
            g = self.cmap.get(ord(ch))
            if g:
                w += self.hmtx[g][0] * s
        return w

    def path_ops(self, text, size, x, baseline):
        s = size / self.upem
        ops: list[str] = []
        curx = x
        for ch in text:
            g = self.cmap.get(ord(ch))
            if not g:
                continue
            pen = OpsPen(self.gs, s, curx, baseline)
            self.gs[g].draw(pen)
            ops.extend(pen.ops)
            curx += self.hmtx[g][0] * s
        return ops

def add_path_stream(doc, page, ops, color=(0, 0, 0)):
    r, g, b = color
    head = f"{r:.3f} {g:.3f} {b:.3f} rg\n"
    body = "\n".join(ops) + "\n" + "f*\n"
    return head + body

def commit(doc, page, content: str):
    """把自定义绘图指令接到页面内容流后面。"""
    page.wrap_contents()
    kind, val = doc.xref_get_key(page.xref, "Contents")
    refs = []
    if kind == "xref":
        refs = [val.strip()]
    elif kind == "array":
        nums = val.strip().strip("[]").split()
        refs = [" ".join(nums[i:i + 3]) for i in range(0, len(nums), 3) if len(nums[i:i + 3]) == 3]
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1", "ignore"))
    allrefs = " ".join(refs) + f" {xref} 0 R"
    doc.xref_set_key(page.xref, "Contents", f"[{allrefs}]")

def draw_month(page, year: int, month: int, digit: Face, cn: Face,
               drop_day: int | None = None, wrong_day: tuple | None = None,
               shift: int = 0):
    doc = page.parent
    ops: list[str] = []

    title = f"{year}"
    ops += cn.path_ops(title, 11, X0, H - 30)
    mlabel = f"{month}月"
    ops += cn.path_ops(mlabel, 30, X0, H - 62)

    heads = ["一", "二", "三", "四", "五", "六", "日"]
    for c, ch in enumerate(heads):
        w = cn.advance(ch, 10)
        ops += cn.path_ops(ch, 10, X0 + c * PITCH + (PITCH - w) / 2, YTOP + 42)

    days = calendar.monthrange(year, month)[1]
    first_wd = datetime.date(year, month, 1).weekday()
    offset = first_wd + shift

    for d in range(1, days + 1):
        if drop_day is not None and d == drop_day:
            continue
        idx = offset + d - 1
        row, col = idx // 7, idx % 7
        baseline = YTOP - row * ROWH
        text = str(d)
        if wrong_day and wrong_day[0] == d:
            text = str(wrong_day[1])
        w = digit.advance(text, NUM_SIZE)
        cx = X0 + col * PITCH + (PITCH - w) / 2
        ops += digit.path_ops(text, NUM_SIZE, cx, baseline)
        small = "初一"
        sw = cn.advance(small, SMALL_SIZE)
        ops += cn.path_ops(small, SMALL_SIZE,
                           X0 + col * PITCH + (PITCH - sw) / 2, baseline - 18)

    commit(doc, page, add_path_stream(doc, page, ops))

def build(out_path: str, year: int = 2027):
    doc = fitz.open()
    digit = Face(FONT_DIGIT)
    cn = Face(FONT_CN, 0)

    p = doc.new_page(width=W, height=H)
    ops = cn.path_ops(f"{year}", 96, 120, 260)
    ops += cn.path_ops("台历", 34, 200, 170)
    commit(doc, p, add_path_stream(doc, p, ops, (0.1, 0.1, 0.1)))

    for m in range(1, 13):
        p = doc.new_page(width=W, height=H)
        kw = {}
        if m == 3:
            kw["wrong_day"] = (17, 11)   # 17 日印成 11
        if m == 7:
            kw["shift"] = 1              # 整月右移一列
        if m == 11:
            kw["drop_day"] = 23          # 漏掉 23 日
        draw_month(p, year, m, digit, cn, **kw)

    doc.save(out_path, deflate=True, garbage=3)
    doc.close()
    return out_path

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "samples/台历2027_测试.pdf"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    build(out)
    print("已生成", out)
