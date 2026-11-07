"""板端版本兼容性探针：用 rknn-toolkit-lite2 加载【由 toolkit2 2.3.2 转出的】.rknn，
在板上那台 runtime 为 librknnrt 2.1.0 的 RK3576 上真跑一次 forward。

目的：判定 BOARD_BRINGUP §11.4 的路线 A（沿用 2.1.0 runtime）还是路线 B（升级 runtime）。
本脚本只碰 NPU 与 /root 下的一个探针模型，不改任何系统库。

用法（板上）:  python3 /root/_probe_board.py [模型路径]
"""
import sys

import numpy as np
from rknnlite.api import RKNNLite

MODEL = sys.argv[1] if len(sys.argv) > 1 else "/root/probe_conv_fp16.rknn"

lite = RKNNLite()
print("[probe] lite2 version:", getattr(RKNNLite, "__version__", "n/a"))
ret = lite.load_rknn(MODEL)
print("[probe] load_rknn ret =", ret)
if ret != 0:
    raise SystemExit(1)

# 2.3.x 的 init_runtime 只认 core_mask；旧版是 target=...，两种都试出来
try:
    ret = lite.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO)
    print("[probe] init_runtime(core_mask=AUTO) ret =", ret)
except TypeError:
    ret = lite.init_runtime(target="rk3576")
    print("[probe] init_runtime(target=rk3576) ret =", ret)
if ret != 0:
    raise SystemExit(2)

try:
    print("[probe] sdk version =", lite.get_sdk_version())
except Exception as exc:                                    # noqa: BLE001
    print("[probe] get_sdk_version 失败:", exc)

x = np.random.randn(1, 3, 8, 8).astype(np.float32)
# lite2 的推理接口叫 inference()；forward() 不存在（本脚本上一版写错，不是版本不兼容）
outs = lite.inference(inputs=[x])
if outs is None or outs[0] is None:
    print("[probe] ❌ forward 返回空")
    raise SystemExit(3)
o = np.asarray(outs[0])
print(f"[probe] ✅ forward 成功 shape={o.shape} sum={float(o.sum()):.4f} "
      f"head={np.round(o.ravel()[:3], 4).tolist()}")
lite.release()
