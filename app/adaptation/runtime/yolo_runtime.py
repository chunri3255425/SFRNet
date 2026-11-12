"""YoloRuntime — Stage-1 YOLO 推理后端（多输出，独立于 IRuntimeAdapter）。

与 IRuntimeAdapter 的区别：SFRNet 是单输出 [1,1,H,W]；YOLO 裸主干是
三尺度 raw 特征图列表（SDD §4.1 A2：decode/NMS 不进 NPU）。
因此不强行套 IRuntimeAdapter 契约，本类只服务 YoloScreenGate。

  backend="rknnlite" 板端：RKNNLite，输入 NHWC float32 **0..255**
                            （归一化 /255 已在转换时烘进 .rknn，A1 口径）
  backend="onnx"     开发机：onnxruntime，输入 NCHW float32 **0..1**
                            （gate 侧负责除 255，见 normalized_input）

infer 返回 list[np.ndarray]，顺序与 yolo_postprocess.STRIDES 一一对应。
"""

from __future__ import annotations

import numpy as np

YOLO_IN_SHAPE = (1, 3, 640, 640)


class YoloRuntimeError(RuntimeError):
    """YOLO 后端加载/推理失败（gate 捕获后按 P3 降级，不致命）。"""


class YoloRuntime:
    def __init__(self, backend: str = "rknnlite"):
        if backend not in ("rknnlite", "onnx"):
            raise YoloRuntimeError(f"未知 YOLO 后端: {backend}")
        self._backend = backend
        self._session = None
        self._rknn = None
        self._input_name = ""

    def load(self, model_path: str) -> None:
        if self._backend == "onnx":
            try:
                import onnxruntime as ort
            except ImportError as e:
                raise YoloRuntimeError("未安装 onnxruntime") from e
            try:
                self._session = ort.InferenceSession(
                    model_path, providers=["CPUExecutionProvider"])
                self._input_name = self._session.get_inputs()[0].name
            except Exception as e:
                raise YoloRuntimeError(f"YOLO ONNX 加载失败: {e}") from e
            return
        # 板端 rknnlite
        try:
            from rknnlite.api import RKNNLite
        except ImportError as e:
            raise YoloRuntimeError("未安装 rknnlite（板端依赖 rknn-toolkit-lite2）") from e
        try:
            rknn = RKNNLite()
            if rknn.load_rknn(model_path) != 0:
                raise YoloRuntimeError(f"加载 {model_path} 失败")
            if rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2) != 0 \
                    and rknn.init_runtime() != 0:
                raise YoloRuntimeError("RKNNLite runtime 初始化失败")
            self._rknn = rknn
        except YoloRuntimeError:
            raise
        except Exception as e:
            raise YoloRuntimeError(f"RKNNLite 初始化异常: {e}") from e

    def infer(self, nchw: np.ndarray) -> list[np.ndarray]:
        """[1,3,640,640] float32 → [feat_s8, feat_s16, feat_s32] raw 特征图列表。"""
        if self._backend == "onnx":
            if self._session is None:
                raise YoloRuntimeError("模型未加载")
            return self._session.run(None, {self._input_name: nchw})
        if self._rknn is None:
            raise YoloRuntimeError("模型未加载")
        nhwc = np.ascontiguousarray(np.transpose(nchw, (0, 2, 3, 1)))
        outputs = self._rknn.inference(inputs=[nhwc])
        # RKNN 可能返回 NHWC 布局的特征图，统一转回 NCHW（与导出 ONNX 对齐）
        fixed = []
        for o in outputs:
            if o.ndim == 4 and o.shape[1] not in (0,) and o.shape[-1] not in (65,):
                o = np.transpose(o, (0, 3, 1, 2))
            fixed.append(np.asarray(o, dtype=np.float32))
        return fixed

    def warmup(self) -> None:
        zeros = np.zeros(YOLO_IN_SHAPE, dtype=np.float32)
        if self._backend == "onnx":
            self.infer(zeros)
        else:
            self._rknn.inference(inputs=[np.transpose(zeros, (0, 2, 3, 1))])

    def unload(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None
        self._session = None
