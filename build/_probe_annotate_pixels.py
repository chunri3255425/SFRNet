#!/usr/bin/env python3
"""逐像素概率标注图（模型的真实输出粒度）。

口径依据（真源）：postprocess.py:74 prob_map = sigmoid(logits) 形状 [1,1,H,W]，
:77 binary_mask = prob_map > threshold，:84 coverage = mask.sum()/mask.numel()
—— 判定层从头到尾只有"像素"这一级，滑窗仅是输入侧拼图方式，不存在块级判决。
所以本图不画块均值；滑窗矩形只用来**挑放大区域**（取逐像素均值最高/最低的两个窗口）。

用法：python build/annotate_pixels.py clean_0064 moire_0292 moire_0293
"""
from __future__ import annotations

import csv
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "build", "probes", "annot")
THRS = (0.5, 0.8, 0.9)
PANEL_W = 380


def build_service():
    import app.adaptation as ada
    from app.logic_layer.config import ConfigManager
    from app.logic_layer.inference_service import InferenceService
    from app.model_layer.registry import ModelRegistry

    cm = ConfigManager()
    io = ada.make_device_io(ada.detect_platform())
    cm.merge(io.read_config() or {})
    cm.merge({"runtime": {"backend": "torch"}, "inference": {"device": "cuda:0"}})
    return InferenceService(ModelRegistry(), cm), cm


def load_manifest():
    with open(os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv"), encoding="utf-8") as f:
        return {os.path.splitext(r["new_name"])[0]: r for r in csv.DictReader(f)}


def run_one(svc, cm, path):
    from app.logic_layer.inference_service import InferenceRequest

    inf, mdl = cm.config.inference, cm.config.model
    params = dict(model_id=mdl.active, tile_size=inf.tile_size, stride=inf.stride,
                  threshold=inf.threshold, coverage_threshold=inf.coverage_threshold,
                  device=inf.device, apply_morphology=inf.apply_morphology)
    with Image.open(path) as im:
        im.load()
        prob = svc.run(InferenceRequest(source=im, **params)).prob_map
        prob = prob.squeeze().detach().float().cpu().numpy()
        rgb = np.asarray(im.convert("RGB"))
    return prob, rgb


def resize(img, w):
    h = max(1, int(img.shape[0] * w / img.shape[1]))
    return cv2.resize(img, (w, h))


def label(img, text, org=(6, 20), color=(255, 255, 255), sc=0.45):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, sc, (0, 0, 0), 3)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, sc, color, 1)
    return img


def heat(prob, size):
    u8 = (np.clip(prob, 0, 1) * 255).astype(np.uint8)
    return cv2.resize(cv2.applyColorMap(u8, cv2.COLORMAP_JET), size)


def mask(prob, t, size):
    return cv2.resize((prob > t).astype(np.uint8) * 255, size, interpolation=cv2.INTER_NEAREST)


def hist_panel(p):
    W, H = 500, 300
    c = np.full((H, W, 3), 245, np.uint8)
    n, e = np.histogram(p, bins=60, range=(0, 1))
    y = np.log1p(n)
    mx = max(1e-6, y.max())
    for i, v in enumerate(y):
        x0 = 45 + int(i * (W - 70) / 60)
        x1 = 45 + int((i + 1) * (W - 70) / 60) - 1
        hh = int(v / mx * (H - 90))
        cv2.rectangle(c, (x0, H - 55 - hh), (x1, H - 55), (200, 120, 40), -1)
    for t, col in ((0.5, (0, 0, 200)), (0.8, (0, 150, 0)), (0.9, (0, 0, 0))):
        x = 45 + int(t * (W - 70))
        cv2.line(c, (x, 20), (x, H - 55), col, 2)
        label(c, "thr=%.1f cov=%.4f" % (t, (p > t).mean()), (x - 40, 15 + 12 * [0.5, 0.8, 0.9].index(t)), col, 0.38)
    for q, col in ((0.5, (60, 60, 60)), (0.9, (60, 60, 60)), (0.99, (60, 60, 60))):
        v = float(np.quantile(p, q))
        x = 45 + int(v * (W - 70))
        cv2.line(c, (x, H - 55), (x, H - 40), col, 2)
        label(c, "p%s=%.3f" % (int(q * 100), v), (x - 30, H - 25), col, 0.35)
    label(c, "per-pixel prob histogram (log count)", (45, H - 4), (30, 30, 30), 0.4)
    return c


def crop_trio(rgb, prob, y, x, tag):
    """原生 512x512 窗口：原图 | 逐像素热图 | >0.8 掩膜，附该窗口逐像素统计。"""
    s = 512
    y2, x2 = min(y + s, rgb.shape[0]), min(x + s, rgb.shape[1])
    sub = prob[y:y2, x:x2]
    base = cv2.cvtColor(np.ascontiguousarray(rgb[y:y2, x:x2]), cv2.COLOR_RGB2BGR)
    w = 300
    hh = int(base.shape[0] * w / base.shape[1])
    panels = [cv2.resize(base, (w, hh)),
              cv2.resize(heat(sub, (w, hh)), (w, hh)),
              cv2.cvtColor(cv2.resize(mask(sub, 0.8, (w, hh)), (w, hh)), cv2.COLOR_GRAY2BGR)]
    out = np.hstack(panels)
    txt = "%s box=(%d,%d) pixel mean=%.3f p90=%.3f max=%.3f cov>0.8=%.3f cov>0.9=%.3f" % (
        tag, x, y, sub.mean(), np.quantile(sub, 0.9), sub.max(), (sub > 0.8).mean(), (sub > 0.9).mean())
    band = np.full((26, out.shape[1], 3), 0, np.uint8)
    label(band, txt[:160], (4, 18), (255, 255, 255), 0.4)
    return np.vstack([band, out])


def pick_windows(prob, h, w):
    """用产品真实滑窗矩形挑最高/最低均值区域（仅用于选区，不参与任何判决）。"""
    from app.logic_layer.sliding_window import SlidingWindowEngine

    try:
        rects = SlidingWindowEngine().plan(h, w)
    except IndexError:
        return None, None
    scored = []
    for r in rects:
        sub = prob[r.slice]                    # Rect 自带 slice，属性名是 x/y/width/height
        if sub.size:
            scored.append((float(sub.mean()), int(r.y), int(r.x)))
    if not scored:
        return None, None
    scored.sort()
    return scored[-1], scored[0]


def main():
    names = sys.argv[1:] or ["clean_0064", "moire_0292", "moire_0293"]
    os.makedirs(OUTDIR, exist_ok=True)
    man = load_manifest()
    svc, cm = build_service()
    for nm in names:
        r = man[nm]
        path = os.path.join(ROOT, r["old_dir"], r["new_name"])
        prob, rgb = run_one(svc, cm, path)
        h, w = prob.shape
        sw = PANEL_W
        sh = int(sw * h / w)
        row1 = np.hstack([
            label(cv2.cvtColor(resize(rgb, sw), cv2.COLOR_RGB2BGR).copy(), "original", color=(255, 255, 255)),
            label(heat(prob, (sw, sh)), "prob_map sigmoid per-pixel"),
            label(cv2.cvtColor(mask(prob, 0.5, (sw, sh)), cv2.COLOR_GRAY2BGR), "mask prob>0.5  cov=%.4f" % (prob > 0.5).mean()),
            label(cv2.cvtColor(mask(prob, 0.8, (sw, sh)), cv2.COLOR_GRAY2BGR), "mask prob>0.8  cov=%.4f" % (prob > 0.8).mean()),
            label(cv2.cvtColor(mask(prob, 0.9, (sw, sh)), cv2.COLOR_GRAY2BGR), "mask prob>0.9  cov=%.4f" % (prob > 0.9).mean()),
        ])
        band = np.full((28, row1.shape[1], 3), 0, np.uint8)
        label(band, "%s | %s | %dx%d pixels | mean=%.4f p50=%.4f p90=%.4f p99=%.4f max=%.4f" % (
            nm, r["category"], w, h, prob.mean(), np.quantile(prob, .5), np.quantile(prob, .9),
            np.quantile(prob, .99), prob.max()), (4, 19), (255, 255, 255), 0.5)
        rows = [band, row1]  # noqa: F841  保留命名以便阅读：上行=标题条+五面板
        hp = hist_panel(prob)
        hi, lo = pick_windows(prob, h, w)
        crops = []
        if hi:
            crops.append(crop_trio(rgb, prob, hi[1], hi[2], "HIGHEST-mean window"))
        if lo:
            crops.append(crop_trio(rgb, prob, lo[1], lo[2], "LOWEST-mean window"))
        pad_h = max([hp.shape[0]] + [c.shape[0] for c in crops])
        need_w = hp.shape[1] + 8 + sum(c.shape[1] + 8 for c in crops)
        canvas_w = max(row1.shape[1], need_w)
        if canvas_w > row1.shape[1]:
            pad = canvas_w - row1.shape[1]
            band = np.pad(band, ((0, 0), (0, pad), (0, 0)), constant_values=0)
            row1 = np.pad(row1, ((0, 0), (0, pad), (0, 0)), constant_values=30)
        b = np.full((pad_h + 4, canvas_w, 3), 30, np.uint8)
        b[:hp.shape[0], :hp.shape[1]] = hp
        cx = hp.shape[1] + 8
        for c in crops:
            b[:c.shape[0], cx:cx + c.shape[1]] = c
            cx += c.shape[1] + 8
        fig = np.vstack([band, row1, b])
        out = os.path.join(OUTDIR, "annot_%s.png" % nm)
        cv2.imwrite(out, fig)
        print("%s -> %s  %dx%d" % (nm, os.path.relpath(out, ROOT), fig.shape[1], fig.shape[0]))
        print("   逐像素: mean=%.4f p50=%.4f p90=%.4f p99=%.4f max=%.4f | cov>0.5=%.4f >0.8=%.4f >0.9=%.4f" % (
            prob.mean(), np.quantile(prob, .5), np.quantile(prob, .9), np.quantile(prob, .99), prob.max(),
            (prob > .5).mean(), (prob > .8).mean(), (prob > .9).mean()))
        if hi and lo:
            print("   最高窗 (%d,%d) mean=%.4f | 最低窗 (%d,%d) mean=%.4f" % (
                hi[2], hi[1], hi[0], lo[2], lo[1], lo[0]))


if __name__ == "__main__":
    main()
