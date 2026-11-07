"""导出侧的 FRM 静态化变体（只在 ONNX 导出时生效，**不改动训练代码**）。

为什么需要
----
`upstream/sfr/FRM.py:88-99` 的硬路由长这样：

    for i in range(4):
        idx = (preds == i).nonzero(as_tuple=True)[0]
        if len(idx) > 0:
            output[idx] = self.branches[i](block[idx]) * ste_weight[idx]

两个后果（都实测过，见 docs/guides/BOARD_BRINGUP.md §13.3）：
1. `nonzero` 让 ONNX 图带数据相关形状，RKNN-Toolkit2 编译层直接拒收；
2. `if len(idx) > 0` 被 trace 成常量，只有 dummy 命中的那条分支进图 —— 实测 60 个真实
   切片里有 14 个的路由与 dummy 不同，即那些输入下导出物的语义是错的。

本模块的做法
----
把"按索引写回"换成"4 个专家全算 + one-hot 掩码求和"：

    out = Σ_i branch_i(block) * onehot(preds)[i]

**前向数值与原实现逐字等价**（原式里 `ste_weight` 在前向时恰好等于 1.0，选中的行写入
对应分支结果、未选中的行保持 0），但整张图是静态的：没有 NonZero / ScatterND，也没有
被冻结的分支。等价性由本文件的 `--check` 用真实切片实测，不靠这段论证。

训练路径完全不受影响：补丁只在 `torch.onnx.export` 期间挂着，出图即摘除。
"""
from __future__ import annotations

import contextlib

import torch
import torch.nn.functional as F


def _static_process_block_hard_routing(self, block):
    """`FissionReorganizationModule.process_block_hard_routing` 的静态等价实现。"""
    logits = self.router(block)                       # [B, 4]
    preds = torch.argmax(F.softmax(logits, dim=1), dim=1)     # [B]
    onehot = F.one_hot(preds, num_classes=len(self.branches)).to(block.dtype)  # [B, 4]

    out = None
    for i, branch in enumerate(self.branches):
        feat = branch(block)                          # 全算，不用 nonzero 选行
        masked = feat * onehot[:, i].view(-1, 1, 1, 1)
        out = masked if out is None else out + masked
    return out


@contextlib.contextmanager
def static_hard_routing():
    """临时把 FRM 的硬路由换成静态等价实现；退出即还原，训练不受影响。"""
    from upstream.sfr.FRM import FissionReorganizationModule

    original = FissionReorganizationModule.process_block_hard_routing
    FissionReorganizationModule.process_block_hard_routing = _static_process_block_hard_routing
    try:
        yield
    finally:
        FissionReorganizationModule.process_block_hard_routing = original


# ───────────────────────── 等价性实测 ─────────────────────────
def equivalence_report(n_tiles: int = 24, seed: int = 0) -> int:
    """在真实切片上比对【原硬路由】与【静态变体】的整网输出，最大相对误差应为 0。"""
    import sys
    from pathlib import Path

    import numpy as np
    import cv2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.model_layer.registry import ModelRegistry

    IN = 512
    MEAN = np.array([0.485, 0.456, 0.406], np.float32)
    STD = np.array([0.229, 0.224, 0.225], np.float32)

    core, _, _ = ModelRegistry().create(model_id="sfrnet_uhd", device="cpu")
    state = torch.load("assets/weights/SFR_best.pth", map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    core.load_state_dict(state, strict=True)
    net = core._model if hasattr(core, "_model") else core
    net.eval()

    paths = ["samples/moire_photo.jpg", "samples/clean_screenshot.png",
             "samples/clean_ide_screenshot.png", "build/test_moire.jpg"]
    rng = np.random.default_rng(seed)

    worst = 0.0
    flips = 0
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            continue
        h, w = img.shape[:2]
        for _ in range(n_tiles // len(paths)):
            if h <= IN or w <= IN:
                tile = cv2.resize(img, (IN, IN))
            else:
                y = int(rng.integers(0, h - IN)); x = int(rng.integers(0, w - IN))
                tile = img[y:y + IN, x:x + IN]
            x_ = (cv2.cvtColor(tile, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - MEAN) / STD
            x_t = torch.from_numpy(np.transpose(x_, (2, 0, 1))[None].astype(np.float32))
            with torch.no_grad():
                a = net(x_t).numpy()                      # 原硬路由
            with torch.no_grad(), static_hard_routing():
                b = net(x_t).numpy()                      # 静态变体
            rel = float(np.abs(a - b).max() / (np.abs(a).max() + 1e-12))
            worst = max(worst, rel)
            if not np.allclose(a, b, rtol=1e-4, atol=1e-4):
                flips += 1
            print(f"   rel={rel:.3e} {'✅' if rel < 1e-5 else '❌ 不等价'}")
    print(f"[static] 共 {len(paths)} 图源 × {n_tiles // len(paths)} 片："
          f"最大相对误差 = {worst:.3e}，不等价片数 = {flips}")
    return 0 if worst < 1e-5 else 1


if __name__ == "__main__":
    raise SystemExit(equivalence_report())
