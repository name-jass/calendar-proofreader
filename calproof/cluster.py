"""按形状给字形分组 —— 这一步完全不依赖字体。

转曲文件里，同一个数字被"画"了无数次，但每次画的都是同一条路径，
所以它们的形状几乎逐像素相同；不同数字之间则有明显差别。
于是"认字"变成"分组"，再靠日历本身的位置规律给每组取名字。
"""
from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
from PIL import Image

H = 32          # 归一化画布高度
THR = 0.88      # 同形判定阈值（同字≈0.95+，异字≈0.80 以下）


def normalize(mask: np.ndarray) -> np.ndarray:
    """紧贴墨点裁切 → 等比缩放到统一高度 → 水平居中。"""
    if mask is None or mask.size == 0:
        return np.zeros((H, H), np.float32)
    ys, xs = np.where(mask > 0.3)
    if ys.size == 0:
        return np.zeros((H, H), np.float32)
    sub = mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = sub.shape
    if h < 1 or w < 1:
        return np.zeros((H, H), np.float32)
    nw = max(1, min(H, int(round(w * H / float(h)))))
    arr = (sub / max(float(sub.max()), 1e-6) * 255.0).astype(np.uint8)
    im = Image.fromarray(arr).resize((nw, H), Image.LANCZOS)
    a = np.asarray(im, np.float32) / 255.0
    a = (a > 0.35).astype(np.float32)
    out = np.zeros((H, H), np.float32)
    off = (H - nw) // 2
    out[:, off: off + nw] = a
    return out


def _unit(a: np.ndarray) -> np.ndarray:
    v = a.ravel().astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def cluster_shapes(masks: list[np.ndarray], thr: float = THR, iters: int = 8):
    """把一组字形按形状分组。返回 (labels, centroids, best_sim, norms)。"""
    if not masks:
        return [], [], [], []
    norms = [normalize(m) for m in masks]
    V = np.stack([_unit(a) for a in norms])
    n = len(V)
    if n == 1:
        return [0], [V[0]], [1.0], norms

    S = V @ V.T
    np.fill_diagonal(S, -1.0)

    # 1) 撒种子：从"最典型"（与别人最像）的字开始，每次挑与已有种子都不像的
    order = np.argsort(-S.sum(axis=1))
    seeds: list[int] = []
    for i in order:
        if all(S[i, s] < thr for s in seeds):
            seeds.append(int(i))
    if not seeds:
        seeds = [int(order[0])]

    cent = V[seeds].copy()
    labels = np.zeros(n, dtype=int)

    # 2) 最近中心迭代
    for _ in range(iters):
        sims = V @ cent.T
        new = np.argmax(sims, axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for k in range(len(cent)):
            mem = V[labels == k]
            if len(mem):
                c = mem.mean(axis=0)
                nn = float(np.linalg.norm(c))
                cent[k] = c / nn if nn > 1e-9 else c

    sims = V @ cent.T
    best = sims[np.arange(n), labels]

    # 3) 太像的小簇并进大簇，避免把一个数字拆成两组
    counts = Counter(labels.tolist())
    big = [k for k, c in counts.items() if c >= 3]
    if big:
        for k in list(counts):
            if k in big:
                continue
            cs = [float(np.dot(cent[k], cent[b])) for b in big]
            if cs and max(cs) >= thr - 0.03:
                labels[labels == k] = big[int(np.argmax(cs))]

    remap = {old: new for new, old in enumerate(sorted(set(labels.tolist())))}
    labels = np.array([remap[int(v)] for v in labels], dtype=int)
    cents = [cent[old] for old in remap]
    return labels.tolist(), cents, [float(v) for v in best], norms


def label_by_position(pages: list[dict], n_cluster: int, cents=None):
    """靠"这一格该是几号"给每个簇取名字。

    台历的格子是从左到右、从上到下按 1、2、3… 排的，
    所以每一格该是几号是已知的 —— 这就是一份免费的字典。
    单页可能有错，但 12 页一起投票，错的那一格投不过其余的。
    """
    votes: dict[int, Counter] = defaultdict(Counter)
    for page in pages:
        for item in page["sequence"]:
            day, glyphs = item
            if not (1 <= day <= 31) or not glyphs:
                continue
            s = str(day)
            n = len(glyphs)
            if n < len(s) or n > 3:
                continue
            s = s.zfill(n)
            for j, g in enumerate(glyphs):
                votes[g.cid][s[j]] += 1

    table: dict[int, str] = {}
    conf: dict[int, float] = {}
    seen: Counter = Counter()
    for page in pages:
        for _day, glyphs in page["sequence"]:
            for g in glyphs:
                seen[g.cid] += 1

    for cid in range(n_cluster):
        c = votes.get(cid)
        if not c:
            table[cid] = "?"
            conf[cid] = 0.0
            continue
        top = c.most_common(2)
        ch, cnt = top[0]
        total = float(sum(c.values()))
        second = top[1][1] if len(top) > 1 else 0
        table[cid] = ch
        conf[cid] = (cnt - second) / total

    # 第二遍：整本只出现一次、而且和"同名的其他形状"差得远的簇，名字是猜的。
    # 这种形状很可能就是印错的那一位（多出来一个怪数字），所以标成待确认。
    for cid in range(n_cluster):
        if seen[cid] > 1 or cents is None or cid >= len(cents):
            continue
        ch = table.get(cid)
        if ch in (None, "?"):
            continue
        same = [k for k in range(n_cluster)
                if k != cid and table.get(k) == ch and seen[k] >= 2 and k < len(cents)]
        if not same:
            continue
        sim = max(float(np.dot(cents[cid], cents[k])) for k in same)
        if sim < 0.80:
            conf[cid] = 0.0
    return table, conf, votes
