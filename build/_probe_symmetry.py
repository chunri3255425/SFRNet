#!/usr/bin/env python3
"""检验"左右对称的描边"：概率场到底是阶跃型（跟着亮度）还是带通型（跟着边缘的两侧）。

做法：把所有强边缘按亚像素位置对齐，取穿过边缘的剖面平均。
  - 若模型输出的是亮度/平坦度 → 剖面应该是单调阶跃（左右反对称）
  - 若输出的是边缘响应（DoG/LoG 型带通）→ 剖面应该在边缘两侧对称起伏（左右镜像对称）
另外扫一遍 DoG 尺度，看哪个尺度的 |DoG| 最能解释 prob。

用法：python build/probe_symmetry.py [moire_0292 ...]
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "build", "probes", "probmap")
HALF = 16


def pear(a, b):
    x, y = np.asarray(a, np.float64).ravel(), np.asarray(b, np.float64).ravel()
    return float(np.corrcoef(x, y)[0, 1])


def edge_windows(mag, g, axis, n=600, half=HALF, rng=None):
    """找强边缘，并按亮度方向统一翻转，否则阶跃会在平均时自己抵消。"""
    rng = rng or np.random.default_rng(0)
    thr = np.quantile(mag, 0.995)
    ys, xs = np.nonzero(mag > thr)
    if len(ys) == 0:
        return []
    idx = rng.choice(len(ys), size=min(n, len(ys)), replace=False)
    H, W = g.shape
    wins = []
    for i in idx:
        y, x = int(ys[i]), int(xs[i])
        if axis == 0:
            if x < half or x >= W - half:
                continue
            seg = g[y, x - half:x + half + 1]
        else:
            if y < half or y >= H - half:
                continue
            seg = g[y - half:y + half + 1, x]
        # 统一成“左/上暗 → 右/下亮”；翻转判定只用亮度，对 prob 用同一个判定才能对比
        wins.append((y, x, bool(seg[:half].mean() > seg[half + 1:].mean())))
    return wins


def extract(field, wins, axis, half=HALF):
    out = []
    for y, x, flip in wins:
        if axis == 0:
            seg = field[y, x - half:x + half + 1]
        else:
            seg = field[y - half:y + half + 1, x]
        if flip:
            seg = seg[::-1]
        if seg.size == 2 * half + 1 and np.isfinite(seg).all():
            out.append(seg - seg.mean())        # 去掉底座，只留剖面形状
    if len(out) < 30:
        return None, len(out)
    return np.stack(out).mean(0), len(out)


def asym(prof):
    """反对称能量占比：|prof - flip(prof)| / |prof|。纯反对称（阶跃）=2.0，完全左右对称=0.0。"""
    m = prof - prof[::-1]
    return float(np.linalg.norm(m) / (np.linalg.norm(prof) + 1e-9))


def main(names):
    for nm in names:
        p = os.path.join(CACHE, nm + ".npz")
        if not os.path.isfile(p):
            print(nm, "无缓存（先跑 build/plot_probmap.py 生成）")
            continue
        z = np.load(p, allow_pickle=True)
        prob, rgb = z["prob"].astype(np.float32), z["rgb"]
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        print("\n===== %s =====" % nm)
        for axis, lab in ((0, "穿过竖直边缘（左右剖面）"), (1, "穿过水平边缘（上下剖面）")):
            wins = edge_windows(mag, g, axis)
            pl, n1 = extract(g, wins, axis)
            pp, n2 = extract(prob, wins, axis)
            if pl is None or pp is None:
                print("  %s：可用边缘太少（亮度 %d / 概率 %d）" % (lab, n1, n2)); continue
            print("  %s（%d 个对齐窗口）" % (lab, len(wins)))
            print("    亮度剖面   反对称度 %.3f（2.0=纯阶跃）  峰值位置 %+d px" % (asym(pl), int(np.argmax(np.abs(pl)) - HALF)))
            print("    概率剖面   反对称度 %.3f（2.0=跟亮度一样是阶跃；0.0=对称 ridge，那才是边缘检测器）"
                  " 峰值位置 %+d px" % (asym(pp), int(np.argmax(np.abs(pp)) - HALF)))
            print("    概率剖面（每 2px 一点，中心=边缘）: " +
                  " ".join("%+.3f" % v for v in pp[::2]))
            print("    亮度剖面（同）:                  " +
                  " ".join("%+.3f" % v for v in pl[::2]))

        print("  |DoG| 各尺度对 prob 的解释力（逐像素 Pearson）：")
        best = None
        for s1, s2 in ((0.5, 1.5), (1.0, 2.0), (1.5, 3.0), (2.0, 4.0), (3.0, 6.0), (4.0, 8.0), (6.0, 12.0)):
            d = cv2.GaussianBlur(g, (0, 0), s1) - cv2.GaussianBlur(g, (0, 0), s2)
            r = pear(prob, np.abs(d))
            print("    σ=(%.1f,%.1f)  Pearson %+.3f" % (s1, s2, r))
            if best is None or abs(r) > abs(best[2]):
                best = (s1, s2, r)
        print("  最匹配尺度 σ=(%.1f,%.1f) 相关 %+.3f" % best)

        # 整幅镜像对称：把 prob 与它自己的左右镜像比，再拿输入图同样比，排除"内容本来就对称"
        pm = pear(prob[:, HALF:-HALF], prob[:, HALF:-HALF][:, ::-1])
        gm = pear(g[:, HALF:-HALF], g[:, HALF:-HALF][:, ::-1])
        print("  整幅左右镜像自相关：prob %.3f  输入亮度 %.3f  （prob 明显更高才说明对称来自模型而非内容）"
              % (pm, gm))

        # 能不能只用“平滑后的亮度”解释 prob？扫模糊尺度 + 看分位映射是否单调
        print("  prob 对“模糊后亮度”的解释力：")
        bs = None
        for s in (2, 4, 8, 16, 32, 64):
            gg = cv2.GaussianBlur(g, (0, 0), s)
            r = pear(prob, gg)
            print("    σ=%2d  Pearson %+.3f" % (s, r))
            if bs is None or abs(r) > abs(bs[1]):
                bs = (s, r, gg)
        print("    最佳 σ=%d（相关 %+.3f）下，按亮度分位分箱的 mean prob：" % (bs[0], bs[1]))
        q = np.quantile(bs[2], np.linspace(0, 1, 11))
        line = []
        for i in range(10):
            m = (bs[2] >= q[i]) & (bs[2] <= q[i + 1])
            if m.sum() > 1000:
                line.append("%.3f" % prob[m].mean())
        print("      暗→亮 10 箱：" + " ".join(line) + "   ← 单调上升则 prob 就是亮度的函数")


if __name__ == "__main__":
    main(sys.argv[1:] or ["moire_0292", "clean_0064", "moire_0293"])
