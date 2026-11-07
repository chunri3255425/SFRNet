"""端到端数值对齐（x86 侧准备 + 参照）：拿一张真实屏摄图，算 onnxruntime 参照输出，
并把板端要喂的两种输入一并导出，交给 _probe_board_run.py 比对。

为什么要两种输入：.rknn 里烧了 mean/std，板端到底该给"原始 0-255"还是"已经归一化的
浮点"直接决定 app/adaptation/runtime/rknn_runtime.py 的输入契约（SDD §4.1 要点 1）。

    python build/_probe_eval_x86.py            # 产出 build/probes/eval/
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_model import IN_SIZE, MEAN, STD   # noqa: E402

OUT = Path("build/probes/eval")
IMG = Path("build/test_moire.jpg")


def main() -> int:
    import cv2
    import onnxruntime as ort

    bgr = cv2.imread(str(IMG))
    if bgr is None:
        print(f"[eval] 读不到 {IMG}")
        return 1
    rgb = cv2.cvtColor(cv2.resize(bgr, (IN_SIZE, IN_SIZE)), cv2.COLOR_BGR2RGB)
    raw = rgb.astype(np.float32)                                   # 0-255
    norm = (raw / 255.0 - MEAN) / STD                              # 归一化
    x_nchw = np.transpose(norm, (2, 0, 1))[None].astype(np.float32)

    OUT.mkdir(parents=True, exist_ok=True)
    np.save(OUT / "x_nchw_u8.npy", np.transpose(rgb, (2, 0, 1))[None].astype(np.uint8))
    np.save(OUT / "x_nchw_raw.npy", np.transpose(raw, (2, 0, 1))[None].astype(np.float32))
    np.save(OUT / "x_nchw_norm.npy", x_nchw)

    sess = ort.InferenceSession("build/models/sfrnet_sim.onnx", providers=["CPUExecutionProvider"])
    ref = sess.run(None, {"input": x_nchw})[0]
    np.save(OUT / "ref.npy", ref.astype(np.float32))
    print(f"[eval] ref shape={ref.shape} mean={ref.mean():.4f} std={ref.std():.4f} "
          f"min={ref.min():.3f} max={ref.max():.3f}")
    print(f"[eval] 三种输入 + 参照已写入 {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
