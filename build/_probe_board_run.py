"""板端真模型首跑 + 输入契约判定。

跑通只是下限，真正要回答的是两个问题：
  1. 真 SFRNet 在这块板（toolkit 2.3.2 产物 + 厂商 runtime 2.1.0）上能不能跑、耗时多少；
  2. .rknn 的输入到底是 NCHW 还是 NHWC —— SDD §4.1 要点 1 断言"RKNN 固定吃 NHWC"，
     但模型头写的是 `framework layout: NCHW`，两种喂法各跑一次、跟 onnxruntime 参照比，
     谁对谁错一目了然（app/adaptation/runtime/rknn_runtime.py 的 permute 就按结论改）。

用法（板上）:  python3 /root/_probe_board_run.py [模型] [数据目录]
"""
import sys
import time
from pathlib import Path

import numpy as np
from rknnlite.api import RKNNLite

MODEL = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/sfrnet_sim_fp16.rknn")
DATA = Path(sys.argv[2] if len(sys.argv) > 2 else "/root/eval")

x = np.load(str(DATA / "x_nchw_norm.npy"))          # 归一化后的 float32 NCHW，与 ORT 同一份
ref = np.load(str(DATA / "ref.npy"))

CASES = [
    ("NCHW", x,                     "直接喂 NCHW float32（= onnxruntime 的喂法）"),
    ("NHWC", np.transpose(x, (0, 2, 3, 1)), "permute 成 NHWC（= 现在 RknnRuntime 的喂法）"),
]

lite = RKNNLite()
print("[board] load_rknn ret =", lite.load_rknn(str(MODEL)))
print("[board] init_runtime ret =", lite.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO))
print("[board] sdk =", " ".join(lite.get_sdk_version().split()))

for tag, arr, note in CASES:
    try:
        t0 = time.perf_counter()
        outs = lite.inference(inputs=[arr])
        ms = (time.perf_counter() - t0) * 1000
    except Exception as exc:                                    # noqa: BLE001
        print(f"[board] ❌ {tag} ({note}) 抛错: {type(exc).__name__}: {exc}")
        continue
    o = np.asarray(outs[0], dtype=np.float32)
    if o.shape != ref.shape:                                    # 只可能是通道维跑到了最后
        print(f"[board] {tag}: 输出布局 {o.shape} 与参照 {ref.shape} 不同，先按 NHWC 转回")
        o = np.transpose(o, (0, 3, 1, 2))
    rel = np.abs(o - ref).max() / (np.abs(ref).max() + 1e-9)
    corr = float(np.corrcoef(o.ravel(), ref.ravel())[0, 1])
    np.save(DATA / f"out_{tag}.npy", o)
    print(f"[board] {tag}: shape={o.shape} {ms:.1f} ms  最大相对误差={rel:.4f} "
          f"相关系数={corr:.4f}  {'✅ 对上了' if rel < 0.02 else '❌ 对不上'}   <-- {note}")

lite.release()
