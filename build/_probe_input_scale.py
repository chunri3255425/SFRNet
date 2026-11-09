#!/usr/bin/env python3
"""输入尺度对照探针：把整帧缩小后再喂，看逐像素概率图是否获得判别力。

动机（实测得到）：512/256/128 三种 tile 下概率图形态不变（mean 差 ≤0.03），说明"块尺寸"不是根因；
真正的差异在**内容尺度**——实拍 4K 照片里摩尔纹条带的像素周期远大于训练 256 合成 patch 里的周期。
缩小输入是唯一能直接检验这条的路子，而且它指向一条不用重训的产品修法（前处理加定标）。

判别力用三个量看：
  1) 展开度 p99-p50（平场≈0.03，有双峰应显著变大）
  2) 高置信占比 cov>0.9（平场时恒 0，真定位时应只对条纹区非 0）
  3) 类间可分性 = 正类下界 - 负类上界（>0 才存在可用阈值）

用法：python build/probe_input_scale.py [--imgs a,b,c] [--scales 1,0.5,0.25]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv")
OUT = os.path.join(ROOT, "build", "probes", "scale_probe.csv")
DEFAULT_IMGS = ["moire_0292", "moire_0293", "moire_0294", "moire_0296", "moire_0001",
                "clean_0064", "clean_0111", "clean_0002", "clean_0032", "clean_0150"]


def build_service(device):
    import app.adaptation as ada
    from app.logic_layer.config import ConfigManager
    from app.logic_layer.inference_service import InferenceService
    from app.model_layer.registry import ModelRegistry

    cm = ConfigManager()
    io = ada.make_device_io(ada.detect_platform())
    cm.merge(io.read_config() or {})
    cm.merge({"runtime": {"backend": "torch"}, "inference": {"device": device}})
    return InferenceService(ModelRegistry(), cm), cm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imgs", default=",".join(DEFAULT_IMGS))
    ap.add_argument("--scales", default="1,0.5,0.25")
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()

    with open(MANIFEST, encoding="utf-8") as f:
        man = {os.path.splitext(r["new_name"])[0]: r for r in csv.DictReader(f)}
    svc, cm = build_service(args.device)
    inf, mdl = cm.config.inference, cm.config.model
    base_params = dict(model_id=mdl.active, tile_size=inf.tile_size, stride=inf.stride,
                       threshold=inf.threshold, coverage_threshold=inf.coverage_threshold,
                       device=inf.device, apply_morphology=inf.apply_morphology)

    rows = []
    hdr = "%-13s %-5s %6s %9s %6s %6s %6s %6s %8s %8s %8s %7s"
    print(hdr % ("img", "cat", "scale", "frame", "mean", "p50", "p90", "p99", "cov>.5", "cov>.8", "cov>.9", "spread"))
    for nm in args.imgs.split(","):
        r = man[nm]
        path = os.path.join(ROOT, r["old_dir"], r["new_name"])
        for sc in [float(x) for x in args.scales.split(",")]:
            with Image.open(path) as im0:
                im0.load()
                if sc == 1.0:
                    img = im0.convert("RGB")
                else:
                    img = im0.convert("RGB").resize(
                        (max(1, int(im0.width * sc)), max(1, int(im0.height * sc))), Image.LANCZOS)
                from app.logic_layer.inference_service import InferenceRequest
                try:
                    prob = svc.run(InferenceRequest(source=img, **base_params)).prob_map
                    prob = prob.squeeze().detach().float().cpu().numpy()
                except Exception as e:
                    # 缩小会造出“一边≤512、另一边>512”的几何，正好命中 plan() 越界；
                    # 这里不屏蔽，只记录，让缺陷在本表里留下可数的一行。
                    rec = dict(name=nm, category=r["category"], scale=sc,
                               frame="%dx%d" % (img.width, img.height),
                               status="geom_error", err="%s: %s" % (type(e).__name__, str(e)[:60]))
                    rows.append(rec)
                    print("%-13s %-5s %6s %9s  -> 几何越界 %s (%s)" % (
                        nm, r["category"], sc, rec["frame"], rec["err"], img.width <= 512 or img.height <= 512), flush=True)
                    continue
            q = np.quantile(prob, [0.5, 0.9, 0.99])
            rec = dict(name=nm, category=r["category"], scale=sc, frame="%dx%d" % (img.width, img.height),
                       status="ok", err="",
                       mean=round(float(prob.mean()), 4), p50=round(float(q[0]), 4),
                       p90=round(float(q[1]), 4), p99=round(float(q[2]), 4),
                       spread=round(float(q[2] - q[0]), 4),
                       cov05=round(float((prob > 0.5).mean()), 4),
                       cov08=round(float((prob > 0.8).mean()), 4),
                       cov09=round(float((prob > 0.9).mean()), 4))
            rows.append(rec)
            print(hdr % (nm, r["category"], sc, rec["frame"], rec["mean"], rec["p50"], rec["p90"],
                         rec["p99"], rec["cov05"], rec["cov08"], rec["cov09"], rec["spread"]), flush=True)

    cols = ["name", "category", "scale", "frame", "status", "err", "mean", "p50", "p90",
            "p99", "spread", "cov05", "cov08", "cov09"]
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, restval="")
        wr.writeheader()
        wr.writerows(rows)

    print("\n=== 逐尺度类间可分性（正类下界 - 负类上界，>0 才有可用阈值）===")
    for sc in sorted({r["scale"] for r in rows}, reverse=True):
        sub = [r for r in rows if r["scale"] == sc and r.get("status") == "ok"]
        for stat in ("cov08", "cov09", "spread"):
            pos = [r[stat] for r in sub if r["category"] == "moire"]
            neg = [r[stat] for r in sub if r["category"] == "clean"]
            if pos and neg:
                print("scale=%-5s %-7s 正[min=%.4f 中位=%.4f] 负[min=%.4f 中位=%.4f max=%.4f] 可分性=%+.4f" % (
                    sc, stat, min(pos), float(np.median(pos)), min(neg), float(np.median(neg)),
                    max(neg), min(pos) - max(neg)))


if __name__ == "__main__":
    main()
