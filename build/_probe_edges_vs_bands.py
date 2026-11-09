#!/usr/bin/env python3
"""判别"模型输出=对图案描边"这个假设。

第一轮（不占 GPU，直接吃缓存的概率图）：
  把 prob 与三种输入特征对齐看谁更能解释它 —— 亮度、梯度幅值（边缘）、
  拉普拉斯（带通）。若 prob 是"边缘响应"，它对边缘的 AUC 应该远高于对亮度。

第二轮（占 GPU，走产品同一条推理链）：
  造四张极简图，把"有条带"和"有边缘"这两个因素拆开：
    flat      纯平坦灰        —— 两者都无
    bands     纯正弦摩尔条带  —— 有条带、无内容边缘
    edges     纯锐利方块/细线  —— 有强边缘、无周期条带
    hatch     条带+方块        —— 两者都有
  再看真实截图做高斯模糊（σ=1.5：文字边缘被打糊、摩尔条带基本保留）后 prob 掉不掉。

若 prob 跟着 edges 走、bands 与 flat 差不多 ⇒ "描边"成立，模型响应的是边缘而非摩尔纹。
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "build", "probes", "probmap")
MANIFEST = os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv")


def auc(score, label):
    """P(score_正 > score_负)：把 label 当"真值"时 score 的排序能力。"""
    s, l = score.ravel(), label.ravel()
    pos, neg = s[l == 1], s[l == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    # 下采样避免千万级配对（5000×5000 的布尔矩阵约 25 MB）
    step = max(1, len(neg) // 5000)
    neg = neg[::step]
    step = max(1, len(pos) // 5000)
    pos = pos[::step]
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def pear(a, b):
    """显式展平后再算相关。np.corrcoef(二维, 二维) 会把二维数组当“多个变量”处理，
    算出来的不是逐像素相关（上一版就踩了这个坑，三个特征报出同一个值）。"""
    x, y = a.ravel().astype(np.float64), b.ravel().astype(np.float64)
    return float(np.corrcoef(x, y)[0, 1])


def blocks(a, b):
    h, w = a.shape
    H, W = h // b, w // b
    return a[:H * b, :W * b].reshape(H, b, W, b).mean(axis=(1, 3))


def part1():
    import cv2
    print("=== 第一轮：prob 到底跟着亮度、边缘还是纹理密度？===")
    for nm in ("clean_0064", "moire_0292", "moire_0293"):
        p = os.path.join(CACHE, nm + ".npz")
        if not os.path.isfile(p):
            print(nm, "无缓存"); continue
        z = np.load(p, allow_pickle=True)
        prob, rgb = z["prob"].astype(np.float32), z["rgb"]
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        grad = cv2.magnitude(gx, gy)
        edge = (grad >= np.quantile(grad, 0.90)).astype(np.uint8)
        dist = cv2.distanceTransform(1 - edge, cv2.DIST_L2, 3)
        hi = prob > 0.8
        print("\n%s   逐像素 Pearson：亮度%+.3f 梯度%+.3f" %
              (nm, pear(prob, g), pear(prob, grad)))
        print("     AUC(prob 判边缘)=%.3f   >0.8 处到最近边缘距离中位=%.1fpx（全图基线 %.1fpx）"
              % (auc(prob, edge), np.median(dist[hi]) if hi.any() else float("nan"), np.median(dist)))
        print("     mean prob：边缘区 %.3f / 非边缘区 %.3f" % (prob[edge == 1].mean(), prob[edge == 0].mean()))
        print("     分块后（块均值 vs 块均值）的相关：")
        for b in (8, 16, 32, 64):
            pb = blocks(prob, b)
            var = blocks(g * g, b) - blocks(g, b) ** 2          # 块内纹理能量
            print("       %2d×%2d 块  亮度%+.3f  梯度%+.3f  块内方差%+.3f"
                  % (b, b, pear(pb, blocks(g, b)), pear(pb, blocks(grad, b)), pear(pb, var)), flush=True)


def synth(size=1024):
    """把"周期条带"和"锐利边缘"拆开的四张图。"""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    flat = np.full((size, size), 0.5, np.float32)
    b = 0.5 + 0.06 * (np.sin(2 * np.pi * x / 26) + np.sin(2 * np.pi * y / 22)
                      + 0.5 * np.sin(2 * np.pi * (x + y) / 40))
    e = np.full((size, size), 0.5, np.float32)
    rng = np.random.default_rng(0)
    for _ in range(60):                       # 锐利方块：强边缘、无周期
        cx, cy = rng.integers(0, size - 80, 2); w, h = rng.integers(12, 80, 2)
        e[cy:cy + h, cx:cx + w] = float(rng.uniform(0.05, 0.95))
    for _ in range(40):                       # 细线：更极端的边缘
        cy = int(rng.integers(0, size)); e[cy:cy + 2, :] = float(rng.uniform(0, 1))
    h_ = 0.5 * (b - 0.5) + 0.5 * (e - 0.5) + 0.5
    return {"flat": flat, "bands": b, "edges": e, "hatch": h_}


def part2():
    import app.adaptation as ada
    from app.logic_layer.config import ConfigManager
    from app.logic_layer.inference_service import InferenceRequest, InferenceService
    from app.model_layer.registry import ModelRegistry

    cm = ConfigManager()
    io = ada.make_device_io(ada.detect_platform())
    cm.merge(io.read_config() or {})
    cm.merge({"runtime": {"backend": "torch"}, "inference": {"device": "cuda:0"}})
    svc = InferenceService(ModelRegistry(), cm)
    inf, mdl = cm.config.inference, cm.config.model
    P = dict(model_id=mdl.active, tile_size=inf.tile_size, stride=inf.stride,
             threshold=inf.threshold, coverage_threshold=inf.coverage_threshold,
             device=inf.device, apply_morphology=inf.apply_morphology)

    rows = [(k, v) for k, v in synth().items()]
    # 判死实验：纯灰平面（完全没有摩尔纹）。检测器应该全部给低值；
    # 若 prob 随亮度单调上升，则“模型学的是亮度”成立。
    for lv in (0.05, 0.2, 0.4, 0.6, 0.8, 0.95):
        rows.append(("gray_%.2f" % lv, np.full((1024, 1024), lv, np.float32)))
    with open(MANIFEST, encoding="utf-8") as f:
        man = {os.path.splitext(r["new_name"])[0]: r for r in csv.DictReader(f)}
    for nm in ("clean_0064", "moire_0292"):
        r = man[nm]
        with Image.open(os.path.join(ROOT, r["old_dir"], r["new_name"])) as im:
            up = im.convert("L")
        a = np.asarray(up, np.float32) / 255.0
        soft = np.asarray(up.filter(ImageFilter.GaussianBlur(1.5)), np.float32) / 255.0
        rows.append((nm + "_gray", a))
        rows.append((nm + "_soft", soft))                      # 边缘被抹掉、条带基本保留
        rows.append((nm + "_hp", np.clip(a - soft + 0.5, 0, 1)))   # 底座被抹掉、只剩边缘

    print("\n=== 第二轮：拆因素 + 真实图（1024 合成图 9 块/张）===")
    print("%-16s %10s %8s %8s %8s %8s" % ("图", "mean", "p50", "p90", "cov>0.8", "cov>0.9"))
    for name, arr in rows:
        img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).convert("RGB")
        prob = svc.run(InferenceRequest(source=img, **P)).prob_map
        p = prob.squeeze().detach().float().cpu().numpy()
        print("%-16s %10.3f %8.3f %8.3f %8.4f %8.4f"
              % (name, p.mean(), np.quantile(p, .5), np.quantile(p, .9),
                 (p > .8).mean(), (p > .9).mean()), flush=True)


if __name__ == "__main__":
    part1()
    if "--gpu" in sys.argv:
        part2()
    else:
        print("\n（第一轮不占 GPU。加 --gpu 跑第二轮拆因素合成图）")
