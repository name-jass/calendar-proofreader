"""从转曲 PDF 的矢量路径里把"文字块"抠出来。

不渲染整页去找字（会被花纹、照片、边框干扰），
而是直接读 PDF 的绘图指令，只保留"像文字"的那一批路径。
"""
from __future__ import annotations

import os

try:
    import pymupdf as fitz
except ImportError:
    import fitz  # type: ignore

import numpy as np

EPS = 0.4  # 判断两点是否重合的容差（PDF 点）

SYSTEM_FONT_NAMES = [
    "arial.ttf", "arialbd.ttf", "ariblk.ttf",
    "helvetica.ttf", "helveticab.ttf",
    "segoeui.ttf", "segoeuib.ttf",
    "calibri.ttf", "calibrib.ttf",
    "verdana.ttf", "verdanab.ttf",
    "tahoma.ttf", "tahomabd.ttf",
    "trebuc.ttf", "trebucbd.ttf",
    "georgia.ttf", "georgiab.ttf",
    "times.ttf", "timesbd.ttf",
    "constan.ttf", "candarab.ttf",
    "consola.ttf", "framd.ttf",
    "msyh.ttc", "msyhbd.ttc",
    "simhei.ttf", "simsun.ttc",
]


class Glyph:
    """单个字形（一个数字 / 一个汉字）。mask 是它自己的墨点图，不带背景。"""

    __slots__ = ("x0", "y0", "x1", "y1", "mask", "cid", "char")

    def __init__(self, bb, mask):
        self.x0, self.y0, self.x1, self.y1 = bb
        self.mask = mask
        self.cid = -1
        self.char = None

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def __repr__(self) -> str:
        return f"Glyph({self.x0:.1f},{self.y0:.1f},{self.w:.1f}x{self.h:.1f})"


class Item:
    """一个文字块（可能是单个数字，也可能是整个两位数，取决于原文件怎么导出的）。"""

    __slots__ = ("x0", "y0", "x1", "y1", "fill", "color", "stroked")

    def __init__(self, bb, fill, color, stroked=False):
        self.x0, self.y0, self.x1, self.y1 = bb
        self.fill = fill
        self.color = color
        self.stroked = stroked

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def __repr__(self) -> str:
        return f"Item({self.x0:.1f},{self.y0:.1f},{self.x1:.1f},{self.y1:.1f})"


def _acc(box, p):
    x, y = p.x, p.y
    if x < box[0]:
        box[0] = x
    if y < box[1]:
        box[1] = y
    if x > box[2]:
        box[2] = x
    if y > box[3]:
        box[3] = y


def _subpath_boxes(items) -> list[tuple[float, float, float, float]]:
    """把一条绘图指令拆成若干子路径的包围盒。"""
    boxes = []
    cur = None
    last = None
    for it in items:
        op = it[0]
        if op == "l":
            seg = (it[1], it[2])
        elif op == "c":
            seg = (it[1], it[2], it[3], it[4])
        elif op == "re":
            r = it[1]
            boxes.append((r.x0, r.y0, r.x1, r.y1))
            cur = None
            last = None
            continue
        elif op == "qu":
            q = it[1]
            xs = [q.ul.x, q.ur.x, q.ll.x, q.lr.x]
            ys = [q.ul.y, q.ur.y, q.ll.y, q.lr.y]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
            cur = None
            last = None
            continue
        else:
            continue
        start = seg[0]
        if last is None or abs(start.x - last.x) > EPS or abs(start.y - last.y) > EPS:
            if cur is not None:
                boxes.append(tuple(cur))
            cur = [start.x, start.y, start.x, start.y]
        for p in seg:
            _acc(cur, p)
        last = seg[-1]
    if cur is not None:
        boxes.append(tuple(cur))
    return boxes


def _contours(page: fitz.Page):
    """取出页面上所有轮廓（一个闭合图形 = 一条轮廓）。

    会剔除"整页背景板"：很多导出文件（CorelDRAW / AI 常见）会在底下垫一个
    整页白底（或大色块），如果不剔掉，它会把页面上所有字通过"贴得近就合并"
    串成一块整页大的东西，后面按颜色取墨点就只剩下一张白纸。
    """
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out
    pw = max(float(page.rect.width), 1.0)
    ph = max(float(page.rect.height), 1.0)
    page_area = pw * ph

    def _is_background(bb) -> bool:
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if w >= pw * 0.90 and h >= ph * 0.90:
            return True
        if w * h >= page_area * 0.45:
            return True
        return False

    def _is_line(bb) -> bool:
        """表格线 / 分隔线 / 装饰线：又细又长，永远不是字。"""
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        short, long_ = min(w, h), max(w, h)
        if short <= 1.4 and long_ >= 8.0:
            return True
        if short <= 3.0 and long_ >= max(pw, ph) * 0.55:
            return True
        return False

    for d in drawings:
        fill = d.get("fill")
        color = d.get("color")
        if fill is None and color is None:
            continue
        if d.get("fill_opacity") == 0 and d.get("stroke_opacity") == 0:
            continue
        parts = d.get("items") or []
        if not parts:
            r = d["rect"]
            bb = (r.x0, r.y0, r.x1, r.y1)
            if (r.width > 0.15 and r.height > 0.15
                    and not _is_background(bb) and not _is_line(bb)):
                out.append((bb, fill, color))
            continue
        for bb in _subpath_boxes(parts):
            if bb[2] - bb[0] <= 0.15 or bb[3] - bb[1] <= 0.15:
                continue
            if _is_background(bb) or _is_line(bb):
                continue
            out.append((bb, fill, color))
    return out


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _color_key(fill, color):
    """把轮廓按颜色分桶。取不到颜色时统一归到"No color"一桶。"""
    c = fill if fill is not None else color
    if c is None:
        return ("none",)
    return tuple(round(float(v), 2) for v in c[:3])


def collect_items(page: fitz.Page) -> list[Item]:
    """把轮廓合并成"字块"。

    转曲文件常见的形态是整页文字合成一条大路径（一个颜色一段），
    所以必须自己按"贴得够近"把轮廓归到同一个字上：
    字内的笔画、数字内部的封闭孔洞会被并成一块，两个相邻的日期则不会。

    **只在同色轮廓之间合并**：一页上红字、黑字、蓝字常常挨得很近，
    跨色合并出来的字块没法用单色取墨点，表现为"字形数忽多忽少"。
    """
    cs = _contours(page)
    if not cs:
        return []

    heights = [c[0][3] - c[0][1] for c in cs]
    real = [h for h in heights if h >= 1.2]
    med = float(np.median(real)) if real else 1.0
    gap_thr = max(med * 0.30, 0.6)

    buckets: dict[tuple, list[int]] = {}
    for i, (bb, fill, color) in enumerate(cs):
        buckets.setdefault(_color_key(fill, color), []).append(i)

    items: list[Item] = []
    for members in buckets.values():
        idx = sorted(members, key=lambda i: cs[i][0][0])
        uf = _UF(len(idx))
        n = len(idx)
        for a in range(n):
            ia = idx[a]
            ax0, ay0, ax1, ay1 = cs[ia][0]
            ha = ay1 - ay0
            for b in range(a + 1, n):
                ib = idx[b]
                bx0, by0, bx1, by1 = cs[ib][0]
                if bx0 - ax1 > gap_thr:
                    break
                hb = by1 - by0
                if hb < 1.2:
                    continue
                ov = min(ay1, by1) - max(ay0, by0)
                if ov <= 0:
                    continue
                if ov / max(min(ha, hb), 1e-6) < 0.35:
                    continue
                gx = max(0.0, max(ax0, bx0) - min(ax1, bx1))
                if gx <= gap_thr:
                    uf.union(a, b)

        groups: dict[int, list[int]] = {}
        for a in range(n):
            groups.setdefault(uf.find(a), []).append(idx[a])

        for grp in groups.values():
            boxes = [cs[i][0] for i in grp if (cs[i][0][3] - cs[i][0][1]) >= 1.2]
            if not boxes:
                continue
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            if y1 - y0 < 1.2:
                continue
            fill = cs[grp[0]][1]
            color = cs[grp[0]][2]
            items.append(Item((x0, y0, x1, y1), fill, color, fill is None))
    return items


def _split_wide(body: np.ndarray, ratio: float = 1.25, depth: int = 2):
    """把粘连在一起的数字切开。

    "11"、"23" 这种紧排（笔画甚至相碰）的日期，按竖缝切分时会被当成一个字，
    宽度约等于两倍字高。这里在中间找墨点最稀的一列断开 —— 但必须真的存在
    一道"缝"，否则宁可不动（宁可标为待确认，也不要切出假字）。
    """
    h, w = body.shape
    if h < 6 or depth <= 0:
        return [(0, body)]
    if not (ratio * h < w <= 3.3 * h):
        return [(0, body)]
    lo = max(1, int(w * 0.30))
    hi = min(w - 1, int(w * 0.70))
    if hi <= lo:
        return [(0, body)]
    ink = body.sum(axis=0)
    peak = float(ink.max())
    if peak <= 0:
        return [(0, body)]
    cut = int(int(np.argmin(ink[lo:hi])) + lo)
    if float(ink[cut]) > 0.40 * peak:
        return [(0, body)]
    if cut < 0.18 * h or (w - cut) < 0.18 * h:
        return [(0, body)]
    out = []
    for dx, piece in _split_wide(body[:, :cut], ratio, depth - 1):
        out.append((dx, piece))
    for dx, piece in _split_wide(body[:, cut:], ratio, depth - 1):
        out.append((cut + dx, piece))
    return out


class PageImage:
    """页面位图 + 坐标缩放。只渲染一次，后面所有裁剪都从这里取。"""

    def __init__(self, page: fitz.Page, target_max: int = 2800):
        rect = page.rect
        base = max(rect.width, rect.height, 1.0)
        zoom = min(4.2, target_max / base)
        zoom = max(zoom, 1.6)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        arr = arr.reshape(pix.height, pix.width, 3)
        self.img = arr
        self.zoom = zoom
        self.width = pix.width
        self.height = pix.height

    def mask_ext(self, items: list[Item], pad: float = 0.8):
        """裁出墨点，并返回像素原点，便于把像素坐标换算回 PDF 坐标。"""
        if not items:
            return None, 0, 0
        x0 = min(i.x0 for i in items) - pad
        y0 = min(i.y0 for i in items) - pad
        x1 = max(i.x1 for i in items) + pad
        y1 = max(i.y1 for i in items) + pad
        px0 = max(0, int(x0 * self.zoom))
        py0 = max(0, int(y0 * self.zoom))
        px1 = min(self.width, int(np.ceil(x1 * self.zoom)))
        py1 = min(self.height, int(np.ceil(y1 * self.zoom)))
        if px1 - px0 < 2 or py1 - py0 < 2:
            return None, px0, py0
        crop = self.img[py0:py1, px0:px1].astype(np.float32)
        if crop.size == 0:
            return None, px0, py0

        colors = []
        for i in items:
            c = i.fill if i.fill is not None else i.color
            if c is None:
                continue
            if len(c) == 1:
                c = (c[0], c[0], c[0])
            colors.append(np.array([c[0] * 255.0, c[1] * 255.0, c[2] * 255.0], np.float32))
        if not colors:
            return crop.mean(axis=2) < 128.0, px0, py0

        best = np.full(crop.shape[:2], 1e9, np.float32)
        for c in colors:
            d = np.sqrt(((crop - c) ** 2).sum(axis=2))
            np.minimum(best, d, out=best)
        m = best < 76.0
        if m.sum() < 6:  # 颜色容差没抓到，退回"深色像素"
            m = crop.mean(axis=2) < 128.0
        return m, px0, py0

    def mask(self, items: list[Item], pad: float = 0.8) -> np.ndarray | None:
        """按这些字块的填色裁出墨点。背景花纹、照片都会被颜色过滤掉。"""
        return self.mask_ext(items, pad)[0]

    def split_item(self, item: Item, pad: float = 0.3) -> list[Glyph]:
        """把一个字块切成一个个独立字形。

        输出文件常常把整列字（甚至整页字）导出成一条路径，所以必须两级切：
        先按横缝切成"一行一行"，再在每行里按竖缝切成"一个字一个字"。
        """
        m, px0, py0 = self.mask_ext([item], pad)
        if m is None or int(m.sum()) < 4:
            return []
        if float(m.mean()) > 0.80:
            return []

        H_, W_ = m.shape

        def runs(profile, min_gap):
            thr = max(1.0, float(profile.max()) * 0.02)
            out: list[list[int]] = []
            st = None
            for i, v in enumerate(profile):
                if v > thr and st is None:
                    st = i
                elif v <= thr and st is not None:
                    out.append([st, i])
                    st = None
            if st is not None:
                out.append([st, len(profile)])
            merged: list[list[int]] = []
            for r in out:
                if merged and r[0] - merged[-1][1] < min_gap:
                    merged[-1][1] = r[1]
                else:
                    merged.append(list(r))
            return merged

        line_gap = max(2.0, 0.9 * self.zoom)
        lines = runs(m.sum(axis=1), line_gap)

        out: list[Glyph] = []
        for y0, y1 in lines:
            if y1 - y0 < 2:
                continue
            band = m[y0:y1]
            lh = float(y1 - y0)
            glyph_gap = max(1.2, 0.45 * self.zoom)
            parts = runs(band.sum(axis=0), glyph_gap)
            merged: list[list[int]] = []
            for a, b in parts:
                if merged and (b - a) < 0.30 * lh:
                    merged[-1][1] = b
                else:
                    merged.append([a, b])
            for a, b in merged:
                if b - a < 2:
                    continue
                sub = band[:, a:b]
                ys = np.where(sub.any(axis=1))[0]
                if ys.size == 0:
                    continue
                body = sub[ys.min(): ys.max() + 1]
                if body.shape[0] < 2 or body.shape[1] < 2:
                    continue
                for dx, piece in _split_wide(body):
                    if piece.shape[1] < 2:
                        continue
                    out.append(Glyph(
                        ((px0 + a + dx) / self.zoom, (py0 + y0 + ys.min()) / self.zoom,
                         (px0 + a + dx + piece.shape[1]) / self.zoom,
                         (py0 + y0 + ys.max() + 1) / self.zoom),
                        piece.astype(np.float32),
                    ))
        return out


def resolve_fonts(font_dir: str | None) -> list[tuple[str, str, int]]:
    """收集可用字体：用户放的字体优先，然后是系统字体。"""
    out: list[tuple[str, str, int]] = []
    seen = set()

    def add(path, index=0):
        key = (os.path.basename(path).lower(), index)
        if key in seen:
            return
        seen.add(key)
        out.append((os.path.splitext(os.path.basename(path))[0], path, index))

    if font_dir and os.path.isdir(font_dir):
        for name in sorted(os.listdir(font_dir)):
            if name.lower().endswith((".ttf", ".otf", ".ttc")):
                add(os.path.join(font_dir, name))

    windir = os.environ.get("WINDIR") or "C:\\Windows"
    fdir = os.path.join(windir, "Fonts")
    for name in SYSTEM_FONT_NAMES:
        p = os.path.join(fdir, name)
        if os.path.isfile(p):
            add(p)
    return out
