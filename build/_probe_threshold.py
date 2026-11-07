"""阈值扫描：判定层到底是"阈值没调对"还是"模型分不开"。

起因（2026-11-07 产品路径实测）：板端 rknn 与 x86 torch 跑同一张摩尔纹图
coverage 对齐到 1e-6（0.997290 / 0.997286），但**干净截图照样 0.9990 / 0.9815**。
⇒ 转换链无嫌疑，问题在判定或模型本身。本探针把 prob 的完整分布打出来，
直接回答"存在某个 threshold 能分开干净图与摩尔纹图吗"。

融合口径跟上游 predict.py 一致（prob 域均匀平均），不跟 engine 的 logits 域
余弦窗混用——这里要比的是分布，不是复现产品数值。
"""

import sys
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upstream.sfr.sfrnet import build_SFR  # noqa: E402

TILE, STRIDE = 512, 256                     # 与 train.py crop_size=512 / predict.py 一致
IMAGES = ["samples/moire_photo.jpg",        # 唯一的真实摩尔纹正样本
          "samples/clean_screenshot.png",
          "samples/clean_ide_screenshot.png"]
THRESHOLDS = [0.5, 0.7, 0.8, 0.9, 0.95, 0.99]


def prob_map(model: torch.nn.Module, img: np.ndarray) -> np.ndarray:
    """BGR 图 → prob 域均匀平均的 HxW 概率图（逐块推理，同 predict.py）。"""
    h, w = img.shape[:2]
    acc = np.zeros((h, w), np.float32)
    cnt = np.zeros((h, w), np.float32)
    pad_h = max(0, TILE - h)
    pad_w = max(0, TILE - w)
    if pad_h or pad_w:
        img = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)

    def axis(full: int) -> list[int]:
        if full <= TILE:
            return [0]
        out, pos = [], 0
        while pos + TILE <= full:
            out.append(pos)
            pos += STRIDE
        if out[-1] + TILE < full:
            out.append(full - TILE)
        return out

    h0, w0 = h, w                    # 下面 axis() 按原图尺寸走，补边只服务于推理
    acc = acc[:h0, :w0]
    cnt = cnt[:h0, :w0]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    t = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))[None].to(DEVICE)
    with torch.no_grad():
        for y in axis(h0):
            for x in axis(w0):
                tile = t[:, :, y:y + TILE, x:x + TILE]
                p = torch.sigmoid(model(tile)).float().cpu().numpy()[0, 0]
                ph, pw = min(TILE, h0 - y), min(TILE, w0 - x)   # 小图补边后只取回原尺寸
                acc[y:y + ph, x:x + pw] += p[:ph, :pw]
                cnt[y:y + ph, x:x + pw] += 1
    return acc / np.maximum(cnt, 1)


DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = build_SFR().to(DEVICE)
model.load_state_dict(torch.load(ROOT / "assets/weights/SFR_best.pth", map_location=DEVICE))
model.eval()
print(f"[probe] device={DEVICE}  tile={TILE} stride={STRIDE}  融合=prob 域均匀平均")

stats = {}
for rel in IMAGES:
    path = ROOT / rel
    img = cv2.imread(str(path))
    if img is None:
        print(f"  !! 读不到 {rel}")
        continue
    p = prob_map(model, img)
    stats[rel] = p
    qs = np.quantile(p, [0.01, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0])
    print(f"\n{rel}  {img.shape[1]}x{img.shape[0]}")
    print(f"  mean={p.mean():.4f}  分位数 p1/25/50/75/90/99/max = "
          + " ".join(f"{v:.4f}" for v in qs))
    print("  coverage(thr) = "
          + "  ".join(f"{t:g}:{(p > t).mean():.4f}" for t in THRESHOLDS))

if len(stats) > 1:
    print("\n[probe] 阈值可分性（正样本 coverage − 负样本最大 coverage，>0 才算分得开）")
    keys = list(stats)
    pos = keys[0]
    for t in THRESHOLDS:
        c_pos = (stats[pos] > t).mean()
        c_neg = max((stats[k] > t).mean() for k in keys[1:])
        print(f"  thr={t:<5g} 摩尔纹={c_pos:.4f}  干净上界={c_neg:.4f}  差={c_pos - c_neg:+.4f}"
              f"  {'✅可分' if c_pos - c_neg > 0.05 else '❌不可分'}")
