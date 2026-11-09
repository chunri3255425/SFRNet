#!/usr/bin/env python3
"""测试图集分布扫描（本机 torch 后端，走产品同一条推理链）。

为什么直接调 InferenceService 而不是 main.py 的 --detect：
ServiceFacade 只是把张量结果 DTO 化成标量 + PNG，判定用的参数全部从 ConfigManager 取。
这里复用同一套取参（model_id/tile_size/stride/threshold/coverage_threshold/device/apply_morphology），
但把 prob_map 留在手里，于是一次前向能同时算出多档阈值下的覆盖率；走 --detect 反而拿不到概率图。

不自己写前处理：预处理、切块、融合、判定全部由 app 侧代码完成，保证与产品判定可比。

用法：
    python build/scan_distribution.py --per-geom 4      # 每种(类别,块数)取 4 张，先看形状
    python build/scan_distribution.py --all             # 全 494 张
    断点续跑：已在输出 CSV 里的 name 自动跳过
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import app.adaptation as ada  # noqa: E402
from app.logic_layer.config import ConfigManager  # noqa: E402
from app.logic_layer.inference_service import InferenceRequest, InferenceService  # noqa: E402
from app.model_layer.registry import ModelRegistry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv")
TILE, STRIDE = 512, 256
THRS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
COLS = ["name", "category", "width", "height", "tiles", "status", "err",
        "mean_prob", "p50", "p90", "p99", "max_prob", "elapsed_ms",
        "has_moire_default", "coverage_default"] + ["cov_%.1f" % t for t in THRS]


def ntiles(w, h):
    def n(p):
        v = (p - TILE) / STRIDE
        return 0 if v < 0 else math.floor(v + 1e-9) + 1
    return n(w) * n(h)


def build_service():
    """与 main.py 同样的四段合，只把 backend 按到本机 torch。"""
    cm = ConfigManager()
    io = ada.make_device_io(ada.detect_platform())
    cm.merge(io.read_config() or {})
    cm.merge({"runtime": {"backend": "torch"}, "inference": {"device": "cuda:0"}})
    return InferenceService(ModelRegistry(), cm), cm


def one(service, cm, path, thr_default):
    import numpy as np
    inf, mdl = cm.config.inference, cm.config.model
    params = dict(model_id=mdl.active, tile_size=inf.tile_size, stride=inf.stride,
                  threshold=inf.threshold, coverage_threshold=inf.coverage_threshold,
                  device=inf.device, apply_morphology=inf.apply_morphology)
    with Image.open(path) as img:
        img.load()
        t0 = time.perf_counter()
        res = service.run(InferenceRequest(source=img, **params))
        ms = (time.perf_counter() - t0) * 1000.0
        prob = res.prob_map.squeeze().detach().float().cpu()
    p = prob.numpy().ravel()
    q = np.quantile(p, [0.50, 0.90, 0.99])          # 用 numpy；statistics.quantiles 在 1260 万像素上不可用
    row = {"mean_prob": round(float(p.mean()), 6), "max_prob": round(float(p.max()), 6),
           "p50": round(float(q[0]), 6), "p90": round(float(q[1]), 6), "p99": round(float(q[2]), 6),
           "elapsed_ms": round(ms, 1), "has_moire_default": int(res.has_moire),
           "coverage_default": round(float(res.coverage), 6)}
    for t in THRS:
        row["cov_%.1f" % t] = round(float((p > t).mean()), 6)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-geom", type=int, default=0, help="每种(类别,块数)取 N 张")
    ap.add_argument("--all", action="store_true", help="全跑")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "probes", "dist_scan.csv"))
    args = ap.parse_args()

    rows = list(csv.DictReader(open(MANIFEST, encoding="utf-8")))
    for r in rows:
        r["tiles"] = ntiles(int(r["width"]), int(r["height"]))
        r["geom"] = "%s/%s" % (r["category"], r["tiles"])
    done = set()
    if os.path.isfile(args.out):
        done = {x["name"] for x in csv.DictReader(open(args.out, encoding="utf-8"))}

    picked = []
    if args.all:
        picked = rows
    else:
        by = {}
        for r in rows:
            by.setdefault(r["geom"], []).append(r)
        for g in sorted(by, key=lambda k: (k.split("/")[0], int(k.split("/")[1]))):
            picked += by[g][:args.per_geom or 4]
    todo = [r for r in picked if r["new_name"] not in done]
    geoms = sorted({r["geom"] for r in picked})
    print("几何档 %d 种，本轮 %d 张（已完成 %d 跳过），预计耗时见逐行输出" % (
        len(geoms), len(todo), len(done)), flush=True)
    print("档位分布: " + ", ".join("%s=%d" % (g, sum(1 for r in picked if r["geom"] == g)) for g in geoms), flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    new = not os.path.isfile(args.out)
    fh = open(args.out, "a", newline="", encoding="utf-8")
    wr = csv.DictWriter(fh, fieldnames=COLS)
    if new:
        wr.writeheader()

    service, cm = build_service()
    thr = cm.config.inference.threshold
    t_start = time.time()
    for i, r in enumerate(todo, 1):
        rec = {"name": r["new_name"], "category": r["category"], "width": r["width"],
               "height": r["height"], "tiles": r["tiles"], "status": "ok", "err": ""}
        try:
            rec.update(one(service, cm, os.path.join(ROOT, r["old_dir"], r["new_name"]), thr))
        except Exception as e:
            rec.update({"status": "error", "err": "%s: %s" % (type(e).__name__, str(e)[:120])})
        wr.writerow({k: rec.get(k, "") for k in COLS})
        fh.flush()
        if rec["status"] == "ok":
            print("[%d/%d] %s %sx%s tiles=%s cov@%.1f=%.4f max=%.4f mean=%.4f p99=%.4f %.0fms" % (
                i, len(todo), r["new_name"], r["width"], r["height"], r["tiles"], thr,
                rec["coverage_default"], rec["max_prob"], rec["mean_prob"], rec["p99"],
                rec["elapsed_ms"]), flush=True)
        else:
            print("[%d/%d] %s %sx%s tiles=%s -> %s" % (
                i, len(todo), r["new_name"], r["width"], r["height"], r["tiles"], rec["err"]), flush=True)
    fh.close()
    el = time.time() - t_start
    print("本轮 %d 张用时 %.1f 分钟（%.2f 分钟/张）" % (len(todo), el / 60, el / 60 / max(1, len(todo))), flush=True)
    json.dump({"per_image_s": el / max(1, len(todo)), "minutes_total_494": el / max(1, len(todo)) * 494 / 60},
              open(os.path.join(ROOT, "build", "probes", "dist_scan_timing.json"), "w"))


if __name__ == "__main__":
    main()
