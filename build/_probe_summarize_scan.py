#!/usr/bin/env python3
"""汇总 build/scan_distribution.py 的全量结果：现配置在 494 张实拍上的真实表现。

用法：python build/summarize_scan.py [build/probes/dist_scan_all.csv]
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "probes", "dist_scan_all.csv")

rows = list(csv.DictReader(open(PATH, encoding="utf-8")))
ok = [r for r in rows if r.get("status") == "ok"]
bad = [r for r in rows if r.get("status") != "ok"]

print("读入 %d 行：成功 %d，失败 %d" % (len(rows), len(ok), len(bad)))
if bad:
    kinds = {}
    for r in bad:
        kinds[r["err"].split(":")[0]] = kinds.get(r["err"].split(":")[0], 0) + 1
    print("  失败类型：" + "，".join("%s×%d" % kv for kv in sorted(kinds.items())))


def col(rs, k):
    return np.array([float(r[k]) for r in rs if r.get(k) not in (None, "", "nan")])


def truthy(v):
    """CSV 里 has_moire 写的是 1/0，不是 True/False。"""
    return str(v).strip() in ("1", "1.0", "True", "true", "yes")


M = [r for r in ok if r["category"] == "moire"]
C = [r for r in ok if r["category"] == "clean"]
print("\n=== 现配置（threshold=0.5, coverage_threshold=0.01）的判决结果 ===")
for nm, rs in (("moire 正样本", M), ("clean 负样本", C)):
    if not rs:
        continue
    y = [truthy(r["has_moire_default"]) for r in rs]
    print("%-12s n=%-4d 判“有纹” %d / %d = %.4f" % (nm, len(rs), sum(y), len(y), np.mean(y)))
print("（正样本这个数应接近 1，负样本这个数就是误报率）")

print("\n=== 逐档阈值的类间可分性（cov = 概率>thr 的像素占比）===")
thrs = [k for k in (ok[0] if ok else {}) if k.startswith("cov_")]
print("%-8s %14s %14s %14s %10s" % ("thr", "正样本 min~max", "负样本 min~max", "中位 正/负", "可分性"))
for k in sorted(thrs):
    a, b = col(M, k), col(C, k)
    if len(a) == 0 or len(b) == 0:
        continue
    # 可分性 = 正类下界(p5) − 负类上界(p95)；判决是 cov>thr 判有纹，
    # 所以只有这个值为正，thr 才能落在两类之间；为负就是区间重叠。
    print("%-8s %14s %14s %14s %10.4f"
          % (k.split("_")[1], "%.3f~%.3f" % (a.min(), a.max()), "%.3f~%.3f" % (b.min(), b.max()),
             "%.3f/%.3f" % (np.median(a), np.median(b)), np.quantile(a, .05) - np.quantile(b, .95)))

print("\n=== 概率图整体统计（mean_prob）===")
for nm, rs in (("正样本", M), ("负样本", C)):
    v = col(rs, "mean_prob")
    if len(v):
        print("%-6s n=%-4d mean_prob p5=%.3f p50=%.3f p95=%.3f" % (nm, len(rs), *np.quantile(v, [.05, .5, .95])))

print("\n=== 按分辨率档的失败率（几何缺陷影响面）===")
buckets = {}
for r in rows:
    w = int(r["width"] or 0); h = int(r["height"] or 0)
    short, lng = min(w, h), max(w, h)
    key = "短边≤512 且长边≤512" if lng <= 512 else ("短边≤512 长边>512" if short <= 512 else "两边都>512")
    d = buckets.setdefault(key, [0, 0])
    d[0] += 1
    d[1] += (r.get("status") != "ok")
for k, (n, e) in sorted(buckets.items()):
    print("  %-22s n=%-4d 失败 %d (%.1f%%)" % (k, n, e, 100.0 * e / n))
