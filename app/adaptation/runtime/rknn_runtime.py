"""RKNN Runtime（板端 NPU，Purple Pi OH2 / RK3576，6 TOPS）。

三个实现要点（SDD §4.1）：
  1. 输入布局：RKNN 固定吃 NHWC，与契约 C1（NCHW float32）不一致 ——
     转换在 infer 内部完成（permute + 量化 Scale 变换），不改动 IInputAdapter。
  2. 输出域：.rknn 导出时不加 sigmoid，infer 返回 raw logits（契约 O1）。
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
        # NCHW float32 → NHWC（RKNN 契约）
        nhwc = np.transpose(tensor, (0, 2, 3, 1))
        outputs = self._rknn.inference(inputs=[nhwc])
        # 恢复 [1,1,H,W] logits 布局
        out = outputs[0]
        if out.ndim == 4 and out.shape[1] == 1:      # NHWC [1,H,W,1]
            out = np.transpose(out, (0, 3, 1, 2))
        return out.astype(np.float32)

    def warmup(self) -> None:
        if self._rknn is None:
            raise RuntimeLoadError("模型未加载")
        self._rknn.inference(inputs=[np.zeros(IN_SHAPE, dtype=np.float32)])

    def unload(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None
