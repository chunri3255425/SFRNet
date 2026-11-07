"""路由决策探针：FRM 的硬路由在 trace 导出时只保留了 dummy 那一刻选中的那条专家分支。
本脚本直接记录 router 对每个 block 的 argmax，回答两件事：
  1. 图里有几个 FissionReorganizationModule（对照 ONNX 算子直方图 NonZero=4 的解释）
  2. 换了真实输入，argmax 是否还和 dummy 一致 —— 一致则冻结"碰巧无害"，
     不一致则该 ONNX 对这类输入是错的（而导出自检永远发现不了）。

    python build/_probe_routing.py
"""
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IN_SIZE = 512
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
RECORD: list[int] = []
PROBS: list[list[float]] = []


def to_chw(path: str) -> np.ndarray:
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"读不到图: {path}")
    img = cv2.cvtColor(cv2.resize(img, (IN_SIZE, IN_SIZE)), cv2.COLOR_BGR2RGB)
    x = (img.astype(np.float32) / 255.0 - MEAN) / STD
    return np.transpose(x, (2, 0, 1))[None]


def main() -> int:
    from app.model_layer.registry import ModelRegistry
    from upstream.sfr.FRM import FissionReorganizationModule

    core, _, _ = ModelRegistry().create(model_id="sfrnet_uhd", device="cpu")
    state = torch.load("assets/weights/SFR_best.pth", map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    core.load_state_dict(state, strict=True)
    net = core._model if hasattr(core, "_model") else core
    net.eval()

    frms = [(n, m) for n, m in net.named_modules() if isinstance(m, FissionReorganizationModule)]
    print(f"[frm] 实例数 = {len(frms)}  名称 = {[n for n, _ in frms]}")

    # 记录每次 process_block_hard_routing 的 argmax（= 该 block 选中的专家编号）
    orig = FissionReorganizationModule.process_block_hard_routing

    def patched(self, block):
        logits = self.router(block)
        probs = F.softmax(logits, dim=1)[0]
        RECORD.append(int(probs.argmax()))
        PROBS.append([round(float(v), 3) for v in probs])
        return orig(self, block)

    FissionReorganizationModule.process_block_hard_routing = patched
    try:
        cases = {
            "dummy(高斯随机)": np.random.randn(1, 3, IN_SIZE, IN_SIZE).astype(np.float32),
            "dummy(另一随机种子)": np.random.randn(1, 3, IN_SIZE, IN_SIZE).astype(np.float32),
            "真实摩尔纹照片": to_chw("samples/moire_photo.jpg"),
            "干净屏摄(截图)": to_chw("samples/clean_screenshot.png"),
            "IDE 截图": to_chw("samples/clean_ide_screenshot.png"),
        }
        per_input = {}
        for name, x in cases.items():
            RECORD.clear()
            with torch.no_grad():
                net(torch.from_numpy(x))
            per_input[name] = list(RECORD)
            print(f"[route] {name:22s} 调用 {len(RECORD)} 次 → 选中专家 {RECORD}")

        ref = per_input["dummy(高斯随机)"]
        bad = [k for k, v in per_input.items() if v != ref]
        print("-" * 78)
        if bad:
            print(f"⚠️ 与 dummy 路由不一致的输入：{bad}")
            print("   ⇒ 该 ONNX 只含 dummy 那套分支，对这些输入语义错误")
        else:
            print("✅ 所有测试输入的路由与 dummy 完全一致")
            print("   ⇒ 冻结分支目前'碰巧无害'，但这是因为路由退化/恒定，不是导出正确")

        # 置信差值：若 top1-top2 很小，则“永远选 1”并不稳定，重训或分布外输入会翻车
        RECORD.clear(); PROBS.clear()
        with torch.no_grad():
            net(torch.from_numpy(to_chw("samples/moire_photo.jpg")))
        print("\n[conf] 同一张摩尔纹图 4 个 block 的 softmax：")
        for i, (c, p) in enumerate(zip(RECORD, PROBS)):
            srt = sorted(p, reverse=True)
            print(f"   block{i} → 专家{c}  probs={p}  top1-top2 差值={srt[0] - srt[1]:.3f}")

        # 切片扫描：用大量随机 512 切片检查路由是否会翻到其它专家
        import cv2
        rng = np.random.default_rng(0)
        dist: dict[int, int] = {}
        flips = 0
        tile_path = Path("build/probes/_tile.jpg")
        paths = ["samples/moire_photo.jpg", "samples/clean_screenshot.png",
                 "samples/clean_ide_screenshot.png", "build/test_moire.jpg"]
        for path in paths:
            img = cv2.imread(path)
            if img is None:
                continue
            h, w = img.shape[:2]
            for _ in range(15):
                if h <= IN_SIZE or w <= IN_SIZE:
                    tile = cv2.resize(img, (IN_SIZE, IN_SIZE))
                else:
                    y = int(rng.integers(0, h - IN_SIZE)); x0 = int(rng.integers(0, w - IN_SIZE))
                    tile = img[y:y + IN_SIZE, x0:x0 + IN_SIZE]
                cv2.imwrite(str(tile_path), tile)
                RECORD.clear()
                with torch.no_grad():
                    net(torch.from_numpy(to_chw(str(tile_path))))
                for c in RECORD:
                    dist[c] = dist.get(c, 0) + 1
                if RECORD != ref[:len(RECORD)]:
                    flips += 1
        tile_path.unlink(missing_ok=True)
        print(f"\n[tiles] {len(paths) * 15} 个切片的专家命中分布 = {dist}  "
              f"与 dummy 路由不同的切片数 = {flips}")
    finally:
        FissionReorganizationModule.process_block_hard_routing = orig
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
