#!/usr/bin/env python3
"""测试图集整理与重新编号（2026-11-08）。

原目录里 摩尔纹图片 混着两套命名（手机直出 IMG_日期_时刻 与编号系列 摩尔纹图片NNNN），
普通屏幕截图 是编号 0001–0300 但磁盘只剩 193（缺 107），两套都没法直接被脚本当带标签的数据集。

编号规则（只改名，不改任何字节）：
    测试图片/摩尔纹图片/*    ->  moire_0001 ... moire_0301
    测试图片/普通屏幕截图/*  ->  clean_0001 ... clean_0193
同类内先排手机直出（按文件名时间戳升序），再排原编号系列（按编号升序）。扩展名保持原样。

安全设计：
- plan 先把「旧名 -> 新名 + md5 + 尺寸」写进 manifest CSV，才允许 apply。manifest 是唯一
  真相源，改名只是元数据操作，凭它可完整 revert。
- apply 前全量预检目标名冲突与目标已存在，任一命中整批拒绝。
- verify 重扫全部文件逐条比对 md5，用来证明没有张冠李戴。

用法：
    python build/organize_images.py plan     # 生成清单，不改文件
    python build/organize_images.py apply    # 按清单改名
    python build/organize_images.py verify   # 改名后校验 md5 与计数
    python build/organize_images.py revert   # 按清单改回旧名
"""
from __future__ import annotations

import collections
import csv
import hashlib
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "docs", "dataset", "test_images_manifest.csv")
CATEGORIES = [
    (os.path.join("测试图片", "摩尔纹图片"), "moire"),
    (os.path.join("测试图片", "普通屏幕截图"), "clean"),
]
RE_IMG = re.compile(r"^IMG_(\d{8})_(\d{6})")
RE_NUM = re.compile(r"(\d+)\.(jpe?g|png)$", re.I)
RE_NEW = re.compile(r"^(moire|clean)_\d{4}\.(jpg|png)$", re.I)
COLS = ["category", "new_name", "old_dir", "old_name", "series",
        "capture_time", "old_number", "width", "height", "bytes", "md5"]


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def size_of(path):
    """只读文件头取宽高；PIL 不在就留空（不因此阻断改名）。"""
    try:
        from PIL import Image
    except ImportError:
        return None, None
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None, None


def series_of(name):
    """返回 (排序组, 排序键, 拍摄时间, 原编号)。手机直出排在前。

    mtime 全是这批文件被拷进本机的时间（14:27–14:30），不能当拍摄顺序，故编号系列留空。
    """
    m = RE_IMG.match(name)
    if m:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return 0, dt.isoformat(sep=" "), dt.strftime("%Y-%m-%d %H:%M:%S"), ""
    m2 = RE_NUM.search(name)
    return 1, "%06d" % (int(m2.group(1)) if m2 else 0), "", (m2.group(1) if m2 else "")


def build_plan():
    rows = []
    for sub, prefix in CATEGORIES:
        d = os.path.join(ROOT, sub)
        if not os.path.isdir(d):
            sys.exit("目录不存在：%s" % sub)
        names = sorted((n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n))),
                       key=lambda n: series_of(n)[:2] + (n,))
        for i, n in enumerate(names, 1):
            grp, _, cap, oldnum = series_of(n)
            p = os.path.join(d, n)
            w, h = size_of(p)
            rows.append({"category": prefix,
                         "new_name": "%s_%04d.%s" % (prefix, i, n.rsplit(".", 1)[-1].lower()),
                         "old_dir": sub.replace(os.sep, "/"), "old_name": n,
                         "series": "capture" if grp == 0 else "legacy_numbered",
                         "capture_time": cap, "old_number": oldnum,
                         "width": w or "", "height": h or "",
                         "bytes": os.path.getsize(p), "md5": md5_of(p)})
    cnt = collections.Counter(r["new_name"] for r in rows)
    bad = [k for k, v in cnt.items() if v > 1]
    if bad:
        sys.exit("新名冲突，拒绝：%s" % bad[:5])
    return rows


def write_manifest(rows):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def read_manifest():
    if not os.path.isfile(MANIFEST):
        sys.exit("缺少 %s，请先跑 plan" % os.path.relpath(MANIFEST, ROOT))
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def plan():
    rows = build_plan()
    write_manifest(rows)
    for _, prefix in CATEGORIES:
        sel = [r for r in rows if r["category"] == prefix]
        print("%-6s %4d 张 -> %s_0001..%s_%04d（手机直出 %d / 原编号系列 %d）" % (
            prefix, len(sel), prefix, prefix, len(sel),
            sum(1 for r in sel if r["series"] == "capture"),
            sum(1 for r in sel if r["series"] == "legacy_numbered")))
    dup = collections.Counter(r["md5"] for r in rows)
    print("内容重复组：%d；清单 %d 行已写入 %s" % (
        sum(1 for v in dup.values() if v > 1), len(rows), os.path.relpath(MANIFEST, ROOT)))


def apply_():
    rows = read_manifest()
    targets = []
    for r in rows:
        d = os.path.join(ROOT, r["old_dir"])
        src, dst = os.path.join(d, r["old_name"]), os.path.join(d, r["new_name"])
        if os.path.exists(dst) and (not os.path.exists(src) or not os.path.samefile(src, dst)):
            sys.exit("目标已存在，拒绝：%s" % r["new_name"])
        targets.append((src, dst))
    done = 0
    for src, dst in targets:
        if os.path.exists(src):
            os.rename(src, dst)
            done += 1
            if done % 100 == 0:
                print("  已改名 %d/%d" % (done, len(rows)))
    print("改名完成 %d/%d" % (done, len(rows)))


def verify():
    rows = read_manifest()
    miss, mismatch, leftover = [], [], []
    for r in rows:
        p = os.path.join(ROOT, r["old_dir"], r["new_name"])
        if not os.path.isfile(p):
            miss.append(r["new_name"])
        elif md5_of(p) != r["md5"]:
            mismatch.append(r["new_name"])
    for sub, _ in CATEGORIES:
        for n in os.listdir(os.path.join(ROOT, sub)):
            if not RE_NEW.match(n):
                leftover.append("%s/%s" % (sub.replace(os.sep, "/"), n))
    print("清单 %d 行 | 缺失 %d | md5 不符 %d | 未按新名命名残留 %d" % (
        len(rows), len(miss), len(mismatch), len(leftover)))
    for lst, tag in ((miss, "缺失"), (mismatch, "md5 不符"), (leftover, "残留")):
        if lst:
            print("  %s: %s" % (tag, lst[:8]))
    sys.exit(0 if not (miss or mismatch or leftover) else 1)


def revert():
    rows = read_manifest()
    done = 0
    for r in rows:
        d = os.path.join(ROOT, r["old_dir"])
        src, dst = os.path.join(d, r["new_name"]), os.path.join(d, r["old_name"])
        if os.path.isfile(src) and not os.path.exists(dst):
            os.rename(src, dst)
            done += 1
    print("已还原 %d/%d" % (done, len(rows)))


if __name__ == "__main__":
    {"plan": plan, "apply": apply_, "verify": verify, "revert": revert}[
        sys.argv[1] if len(sys.argv) > 1 else "plan"]()
