#!/usr/bin/env python3
"""概率图干净画法 v2（替代 annotate_pixels.py 的拼贴版）。

改进点针对上一版的可读性问题：
  - 伪彩带固定 vmin=0/vmax=1 的色标，颜色可以定量读（上一版没有色标）
  - 文字全部在图外的信息条里，不压在像素上
  - 只保留三种视图：原图 | 概率图(色标) | 原图+等值线(0.8/0.9)
  - prob_map 落 .npz 缓存（含 prob 与 rgb），改画法不必重跑模型（--from-cache）

用法：
    python build/plot_probmap.py clean_0064 moire_0292 moire_0293
    python build/plot_probmap.py clean_0064 --from-cache
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv")
CACHE = os.path.join(ROOT, "build", "probes", "probmap")
FIGDIR = os.path.join(ROOT, "build", "probes", "fig")


def _cols(ch):
    """全角字符占 2 列，其余 1 列。"""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def wrap_cjk(text, budget=108):
    """按显示列宽自己折行。matplotlib 的 wrap=True 按字符数折，
    中文一个字算 1 个字符却占 2 列宽，于是折得太晚会冲出右边界。"""
    lines = []
    for raw in text.split("\n"):
        cur, n = "", 0
        for ch in raw:
            c = _cols(ch)
            if n + c > budget and cur:
                lines.append(cur)
                cur, n = "", 0
            cur += ch
            n += c
        lines.append(cur)
    return "\n".join(lines)


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return {os.path.splitext(r["new_name"])[0]: r for r in csv.DictReader(f)}


def infer(names):
    """跑真实推理链，把 prob_map 与缩略原图存进缓存。"""
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
    man = load_manifest()
    os.makedirs(CACHE, exist_ok=True)
    for nm in names:
        r = man[nm]
        path = os.path.join(ROOT, r["old_dir"], r["new_name"])
        with Image.open(path) as im:
            im.load()
            prob = svc.run(InferenceRequest(source=im, **P)).prob_map
            prob = prob.squeeze().detach().float().cpu().numpy()
            rgb = np.asarray(im.convert("RGB"))
        np.savez_compressed(os.path.join(CACHE, nm + ".npz"), prob=prob, rgb=rgb,
                            category=r["category"], src=os.path.join(r["old_dir"], r["new_name"]))
        print("cached %s %s" % (nm, prob.shape), flush=True)


def pick_cjk_font():
    """有中文字体就用中文标图（图上说明给人看的，中文清楚得多）。"""
    from matplotlib import font_manager
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Noto Sans CJK SC", "Microsoft YaHei", "Noto Sans CJK JP",
                 "Droid Sans Fallback", "SimHei"):
        if cand in installed:
            return cand
    return None


def plot(nm, maxw=760):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    font = pick_cjk_font()
    zh = font is not None
    if zh:
        plt.rcParams["font.family"] = font
        plt.rcParams["axes.unicode_minus"] = False

    z = np.load(os.path.join(CACHE, nm + ".npz"), allow_pickle=True)
    prob, rgb, cat = z["prob"], z["rgb"], str(z["category"])
    h, w = prob.shape
    sc = min(1.0, maxw / w)
    rgb_s = np.asarray(Image.fromarray(rgb).resize((int(w * sc), int(h * sc)), Image.LANCZOS))
    prob_s = np.asarray(Image.fromarray((prob * 255).astype(np.uint8)).resize(
        (int(w * sc), int(h * sc)), Image.BILINEAR), np.float32) / 255.0

    q = np.quantile(prob, [0.5, 0.9, 0.99])
    c5, c8, c9 = (float((prob > t).mean()) for t in (0.5, 0.8, 0.9))
    cname = "摩尔纹实拍" if cat == "moire" else "干净屏幕截图"

    num = ("mean=%.3f　p50=%.3f　p90=%.3f　p99=%.3f　max=%.3f　最低=%.3f（注意不是 0）\n"
           % (prob.mean(), q[0], q[1], q[2], prob.max(), float(prob.min())))
    if cat == "moire":
        head = ("【怎么读】深蓝才表示“这里没有摩尔纹”。本图整片橙色、一格深蓝都没有"
                "　→　模型从没在任何像素上说过“没纹”。\n")
        if c8 < 0.3:
            see = ("【看到什么】底座上只有幅度约 ±0.05 的小调制，跨不过 0.8；"
                   "而且调制方向在两类图上不一致（本图边缘处略高，白底截图上明显更低）"
                   "　→　它不是稳定的“图案响应”，只是底座被内容轻微扰动。\n")
        else:
            see = ("【看到什么】中栏几乎整屏都顶到橙红（>0.8 占比 %.4f），红线找不到起点也找不到终点"
                   "　→　本帧的条纹位置信息基本上为零，只是底座刚好被顶过了 0.8。\n" % c8)
        verdict = ("【产品判定】概率>0.5 的像素占比 = %.4f，远超门限 0.01　→　判“有纹”（这张确有纹）。"
                   "但全量 178 张没纹的截图在同一档也全部超门限（误报率 100%%）。\n"
                   "【换门限也救不了】本图 >0.8 占比 = %.4f，>0.9 占比 = %.4f；"
                   "全量 479 张实测：>0.8 档正样本 0.041~0.903（中位 0.526），"
                   "负样本 0.010~0.947（中位 0.586），七档阈值全部区间重叠。\n"
                   % (c5, c8, c9))
    else:
        head = ("【怎么读】这是一张没有任何摩尔纹的屏幕截图，理应全帧深蓝（概率接近 0）。\n")
        see = ("【看到什么】文字与边框处概率反而下凹（分块相关 −0.58~−0.66，AUC 0.245）"
               "　→　右栏那些贴着图案轮廓的红线，是阈值线切在平坦高底座上产出的，不是模型在描图案。\n")
        verdict = ("【产品判定】概率>0.5 的像素占比 = %.4f，远超门限 0.01　→　误报。"
                   "全量 178 张没纹截图在同一档全部被判“有纹”（误报率 100%%）。\n"
                   "【为什么不算定位错】把门限提到 0.8，占比仍有 %.4f；"
                   "全量实测：正样本 >0.8 为 0.041~0.903（中位 0.526），干净截图为 0.010~0.947（中位 0.586）——"
                   "两个区间套在一起，不存在可用阈值。\n"
                   % (c5, c8))
    legend = ("【图例】中栏每个像素涂一个颜色，对照右侧色标：深蓝=0（模型判“无纹”）、"
              "橙=0.7、深红=0.95。右栏红线围出的是概率跨过 0.8 的地方，洋红虚线是 0.9。\n"
              "【剖面实测】把强边缘对齐后取剖面：概率剖面的反对称度 1.85~1.99（满值 2.0，与亮度剖面同形）　→　"
              "概率是“暗侧低、亮侧高”的阶跃（低通、跟着亮度），不是边缘检测器那种左右对称的 ridge。\n"
              "　　　　　　所以图上的“对称描边”是：闭合的暗图案内部被压低、四周保持高值，"
              "阈值线自然在物体外围闭合成圈——对称性来自图案自身形状，不是模型在检测边界。\n"
              "【判死实验】喂一张纯灰色平面（没有任何内容、没有任何摩尔纹）：mean=0.905，"
              "100% 像素超过 0.8，82% 超过 0.9　→　底座与内容无关。\n"
              "　　　　　　实拍两类图的 mean 只差 0.0~0.17，所以图上的“描边”不是检测输出。\n")
    body = wrap_cjk(legend + head + see + num + verdict)
    if not zh:
        body = wrap_cjk("mean=%.3f p50=%.3f p90=%.3f p99=%.3f max=%.3f min=%.3f | cov>0.5=%.4f cov>0.8=%.4f cov>0.9=%.4f\n"
                        "No dark-blue cell anywhere: the model never outputs a low probability.\n"
                        "Edge-aligned profiles: antisymmetry 1.85~1.99 (max 2.0) = a step following luminance,\n"
                        "not a symmetric ridge, so the outlines come from object shapes, not edge detection."
                        % (prob.mean(), q[0], q[1], q[2], prob.max(), float(prob.min()), c5, c8, c9))

    # 文字区高度跟着行数走，否则加几行就会把最后一行裁掉
    text_h = 0.38 * (body.count("\n") + 1) + 0.6
    # 面板高度按图片长宽比算但封顶；否则竖长图会把顶部顶出一大条空白
    panel_h = min(4.9 * h / w, 8.6)
    fig = plt.figure(figsize=(15.5, panel_h + text_h + 0.4))
    gs = fig.add_gridspec(2, 3, height_ratios=[panel_h, text_h], hspace=0.10, wspace=0.06,
                          left=0.02, right=0.98, top=0.93, bottom=0.02)
    fig.suptitle("%s　【%s】　%d×%d 像素，每个像素一个概率" % (nm, cname, w, h), fontsize=22)

    ax = [fig.add_subplot(gs[0, i]) for i in range(3)]
    ax[0].imshow(rgb_s)
    ax[0].set_title("① 原图", fontsize=17, pad=8)
    im1 = ax[1].imshow(prob_s, cmap="turbo", vmin=0.0, vmax=1.0)
    ax[1].set_title("② 逐像素概率图", fontsize=17, pad=8)
    cb = fig.colorbar(im1, ax=ax[1], fraction=0.046, pad=0.02)
    cb.set_ticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    cb.ax.tick_params(labelsize=14)
    ax[2].imshow(rgb_s)
    ext = (0, prob_s.shape[1], prob_s.shape[0], 0)
    for lvl, col, sty in ((0.8, "red", "-"), (0.9, "magenta", "--")):
        ax[2].contour(prob_s, levels=[lvl], colors=col, linestyles=sty, linewidths=1.8, extent=ext)
    ax[2].set_title("③ 红线 = 概率过 0.8", fontsize=17, pad=8)
    for a in ax:
        a.set_xticks([])
        a.set_yticks([])

    ax_txt = fig.add_subplot(gs[1, :])
    ax_txt.axis("off")
    ax_txt.text(0.0, 0.99, body, ha="left", va="top", fontsize=15.5,
                linespacing=1.75, wrap=False,
                bbox=dict(boxstyle="round,pad=0.6", fc="#f6f6f6", ec="#777", lw=1.2))

    os.makedirs(FIGDIR, exist_ok=True)
    out = os.path.join(FIGDIR, "prob_%s.png" % nm)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print("figure -> %s" % os.path.relpath(out, ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=["clean_0064", "moire_0292", "moire_0293"])
    ap.add_argument("--from-cache", action="store_true", help="只用缓存重画，不跑模型")
    args = ap.parse_args()
    need = [n for n in args.names if not os.path.isfile(os.path.join(CACHE, n + ".npz"))]
    if need and not args.from_cache:
        infer(need)
    for nm in args.names:
        if os.path.isfile(os.path.join(CACHE, nm + ".npz")):
            plot(nm)
        else:
            print("%s 无缓存，先跑一次不带 --from-cache" % nm)


if __name__ == "__main__":
    main()
