"""位图（图片）页的字形提取：不靠文字层，直接从像素里切出一个个字。

思路和矢量版一样 —— 先把画面变成"有墨 / 没墨"，再一行一行、一个字一个字切开，
后面所有步骤（认形状、按位置投票命名、对历法）都复用同一套逻辑。
阈值用大津法自动取，不写死；所以浅底深字、深底浅字都能处理。
"""
from __future__ import annotations

import numpy as np

from .extract import Glyph


def _otsu(lum: np.ndarray) -> float:
    """大津法：自动找一个把"字"和"底"分开的亮度阈值。"""
    hist, edges = np.histogram(lum, bins=256, range=(0, 255))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128.0
    p = hist / total
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom <= 1e-12] = 1e-12
    sigma = (mu_t * omega - mu) ** 2 / denom
    k = int(np.argmax(sigma))
    return float(edges[k])


def _runs(profile: np.ndarray, min_gap: int = 1):
    """把一条投影曲线切成若干"有内容"的连续段。"""
    on = profile > 0
    out, st = [], None
    for i, v in enumerate(on):
        if v and st is None:
            st = i
        elif not v and st is not None:
            out.append([st, i])
            st = None
    if st is not None:
        out.append([st, len(on)])
    merged = []
    for a, b in out:
        if merged and a - merged[-1][1] <= min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def glyphs_from_bitmap(arr: np.ndarray, scale: float = 1.0) -> list[Glyph]:
    """从一张位图里切出"像字"的块，返回 Glyph 列表。

    坐标统一换算回 **PDF 点**：位图是渲染出来的，像素坐标要除以渲染倍率
    （scale = 渲染时的 zoom），这样和矢量页的字形坐标是同一套，报告画框才不会偏。
    """
    a = arr.astype(np.float32)
    lum = a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114
    H, W = lum.shape

    thr = _otsu(lum)
    # 大津法在"大面积浅色花纹底"的稿子上会把阈值抬得很高，把底纹也算成墨，
    # 整条日期带就被花纹连成一片。夹一个上限：文字总比花纹深。
    thr = max(70.0, min(175.0, thr))
    ink = lum < thr
    if ink.mean() > 0.5:
        ink = lum >= thr

    # 去掉孤立的噪点：只保留上下左右有邻居的像素
    pad = np.zeros_like(ink)
    pad[1:-1, 1:-1] = (ink[:-2, 1:-1] & ink[2:, 1:-1]
                      & ink[1:-1, :-2] & ink[1:-1, 2:])
    ink = ink & pad

    min_h = max(4, int(0.004 * H))
    max_h = int(0.16 * H)

    glyphs: list[Glyph] = []
    for y0, y1 in _runs(ink.sum(axis=1), min_gap=1):
        if not (min_h <= y1 - y0 <= max_h):
            continue
        band = ink[y0:y1]
        for x0, x1 in _runs(band.sum(axis=0), min_gap=1):
            if x1 - x0 < 2:
                continue
            sub = band[:, x0:x1]
            ys = np.where(sub.any(axis=1))[0]
            xs = np.where(sub.any(axis=0))[0]
            if ys.size == 0 or xs.size == 0:
                continue
            body = sub[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
            if body.shape[0] < min_h or body.shape[1] < 2:
                continue
            dens = float(body.mean())
            if dens < 0.05 or dens > 0.92:      # 几乎没墨 / 整块实心，都不是字
                continue
            glyphs.append(Glyph(
                ((x0 + xs.min()) / scale, (y0 + ys.min()) / scale,
                 (x0 + xs.max() + 1) / scale, (y0 + ys.max() + 1) / scale),
                body.astype(np.float32),
            ))
    return glyphs
