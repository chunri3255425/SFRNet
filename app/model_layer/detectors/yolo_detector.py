"""YoloScreenGate — Stage-1 屏幕检测门控（SDD v2.4.1 §3.3 / §4.2）。

职责边界（设计原则 P1/P2）：
  - 只回答"画面里有没有屏幕"（返回最高置信度 + 判定），不做任何业务解释；
  - 失败抛 YoloGateError，由逻辑层按 P3 降级（禁用级联回退全图），本类不自作主张；
  - letterbox/坐标还原在本类完成，输出 conf 与框均为**原图坐标域**。

流程：RGB 帧 → letterbox 640 → runtime.infer（raw 特征图）→ decode_boxes（numpy，
与 build/export_yolo.py verify 共用同一份 yolo_postprocess）→ 坐标还原 → 门控判定。
"""

from __future__ import annotations

import numpy as np

from app.model_layer.detectors.yolo_postprocess import decode_boxes, letterbox

INPUT_SIZE = 640


class YoloGateError(RuntimeError):
    """Stage-1 门控失败（模型缺失/推理异常）。逻辑层捕获后按 P3 降级。"""


class YoloScreenGate:
    """Stage-1 门控。runtime 需提供 infer(nchw) -> list[raw 特征图]。"""

    def __init__(self, runtime, screen_conf: float = 0.5,
                 gray_zone: float = 0.1, gray_policy: str = "prefer_screen",
                 normalized_input: bool = True,
                 dec_conf_thres: float = 0.05, dec_iou_thres: float = 0.5):
        """
        Args:
            runtime: YoloRuntime（或任何 infer(nchw)->list[np.ndarray] 的对象）
            screen_conf: 门限。conf >= screen_conf → 有屏（短路出 screen_detected）
            gray_zone: 灰区宽度 [screen_conf - gray_zone, screen_conf)
            gray_policy: prefer_screen=灰区视为有屏（宁报不漏，v2.4.1 决议）
                         fallback=灰区视为无屏（继续跑 SFRNet）
            normalized_input: True=gate 内部把像素除 255（ONNX 开发机路径）；
                              False=直接喂 0..255（RKNN：归一化已烘进模型，A1）
            dec_conf_thres/dec_iou_thres: 后处理宽收参数（契约 D2/D3：判决权在门控）
        """
        if not 0.0 < screen_conf < 1.0:
            raise ValueError(f"screen_conf 必须在 (0,1)：{screen_conf}")
        if gray_policy not in ("prefer_screen", "fallback"):
            raise ValueError(f"未知灰区策略: {gray_policy}")
        self._rt = runtime
        self.screen_conf = float(screen_conf)
        self.gray_zone = float(gray_zone)
        self.gray_policy = gray_policy
        self.normalized_input = bool(normalized_input)
        self._dec_conf = dec_conf_thres
        self._dec_iou = dec_iou_thres

    # ── 对逻辑层的两个入口 ─────────────────────
    def screen_conf_max(self, frame_rgb: np.ndarray) -> float:
        """返回本帧屏幕最高置信度（0.0 = 无候选）。失败抛 YoloGateError。"""
        boxes = self.detect(frame_rgb)
        return float(boxes[:, 4].max()) if len(boxes) else 0.0

    def screened(self, conf: float) -> bool:
        """按门限 + 灰区策略判定"是否视为有屏"（v2.4.1：有屏→短路出 screen_detected）。"""
        if conf >= self.screen_conf:
            return True
        if self.gray_policy == "prefer_screen" and \
                conf >= self.screen_conf - self.gray_zone:
            return True
        return False

    # ── 检测主流程 ─────────────────────────────
    def detect(self, frame_rgb: np.ndarray) -> np.ndarray:
        """RGB 帧 → [N,5] (cx, cy, w, h, conf)，原图坐标。失败抛 YoloGateError。"""
        try:
            lb, scale, pad_x, pad_y = letterbox(
                np.ascontiguousarray(frame_rgb), INPUT_SIZE)
            nchw = lb.transpose(2, 0, 1)[None].astype(np.float32)
            if self.normalized_input:
                nchw /= 255.0
            feats = self._rt.infer(nchw)
            boxes = decode_boxes(feats, nc=1, conf_thres=self._dec_conf,
                                 iou_thres=self._dec_iou)
        except YoloGateError:
            raise
        except Exception as e:
            raise YoloGateError(repr(e)) from e
        if len(boxes) == 0:
            return boxes
        # letterbox 域 → 原图域（InputMeta 的 scale/pad 职责收进本类）
        boxes[:, 0] = (boxes[:, 0] - pad_x) / scale
        boxes[:, 1] = (boxes[:, 1] - pad_y) / scale
        boxes[:, 2] /= scale
        boxes[:, 3] /= scale
        return boxes
