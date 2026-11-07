"""RKNN Runtime（板端 NPU，Purple Pi OH2 / RK3576，6 TOPS）。

三个实现要点（SDD §4.1）：
  1. 输入布局：.rknn 吃 **NHWC float32** —— 已上板实测（docs/guides/BOARD_BRINGUP.md §14.4：
     NHWC 喂法相对 onnxruntime 参照：相关系数 1.0000、最大相对误差 0.0059；NCHW 喂法相关系数 0.03）。
     转换在 infer 内部完成（permute），不改动 IInputAdapter；归一化仍在调用侧（契约 C1），
     因为转换时烘的是恒等 mean/std，.rknn 不会再帮你 /255。
  2. 输出域：.rknn 导出时不加 sigmoid，infer 返回 raw logits（契约 O1）。
     实测输出已经是 NCHW [1,1,H,W]，**不要**再反向 permute。
  3. 量化校准集必须用真实屏摄图（build/calib/）。

依赖：rknnlite（板上）或 rknn-toolkit-lite2。未安装时 load 抛 RuntimeLoadError——
factory **不做静默回退**（板端悄悄跑 CPU torch 会让「NPU 已就位」的结论失真），
需要 CPU 兜底请显式 --backend onnx / --backend torch。
"""

from __future__ import annotations

import numpy as np

from app.adaptation.runtime.base import IN_SHAPE, IRuntimeAdapter, RuntimeLoadError


class RknnRuntime(IRuntimeAdapter):
    """RKNN NPU 后端。"""

    def __init__(self):
        self._rknn = None

    def load(self, model_path: str, **kwargs) -> None:
        try:
            from rknnlite.api import RKNNLite
        except ImportError as e:
            raise RuntimeLoadError(
                "未安装 rknnlite（板端 NPU 推理依赖 rknn-toolkit-lite2）"
            ) from e
        try:
            rknn = RKNNLite()
            ret = rknn.load_rknn(model_path)
            if ret != 0:
                raise RuntimeLoadError(f"加载 .rknn 失败，返回码 {ret}")
            # 三核 NPU：优先全核，失败退单核
            ret = rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2)
            if ret != 0:
                ret = rknn.init_runtime()
                if ret != 0:
                    raise RuntimeLoadError("RKNN runtime 初始化失败")
            self._rknn = rknn
        except RuntimeLoadError:
            raise
        except Exception as e:
            raise RuntimeLoadError(f"RKNN 初始化异常: {e}") from e

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        if self._rknn is None:
            raise RuntimeLoadError("模型未加载")
        # NCHW float32 → NHWC（实测已确认 .rknn 要 NHWC）
        nhwc = np.transpose(tensor, (0, 2, 3, 1))
        outputs = self._rknn.inference(inputs=[nhwc])
        out = outputs[0]
        # 实测：单通道输出回来就是 [1,1,H,W]。旧写法“shape[1]==1 就 transpose”
        # 会把对的 NCHW 摇成 [1,H,W,1]，下游后处理拿到错布局还不报错。
        if out.ndim == 4 and out.shape[1] != 1 and out.shape[-1] == 1:
            out = np.transpose(out, (0, 3, 1, 2))
        return out.astype(np.float32)

    def warmup(self) -> None:
        if self._rknn is None:
            raise RuntimeLoadError("模型未加载")
        # 与 infer 同一布局，否则 warm 的是另一条分支，耗时数字不可比
        zeros = np.zeros(IN_SHAPE, dtype=np.float32)
        self._rknn.inference(inputs=[np.transpose(zeros, (0, 2, 3, 1))])

    def unload(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None
