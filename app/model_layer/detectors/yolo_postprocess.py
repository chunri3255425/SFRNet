"""YOLO 后处理（numpy 纯实现）：decode + NMS。

为什么在模型层放一个 numpy 模块（SDD v2.4.1 §4.1 A2）：
  RKNN 侧只跑主干（输出原始特征图），decode + NMS 落 CPU。
  这份实现是**开发机一致性比对**（export_yolo.py 的参照）
  与**板端推理**（yolo_detector.py）共用的同一份逻辑——
  比对和线上跑同一份代码，数字才有可比性。

输入约定（与 build/export_yolo.py 导出的裸主干 ONNX 对齐）：
  raw_outputs: 三个尺度的原始特征图列表，每个 shape [1, 4*reg_max+nc, h, w]
               YOLOv8 单类 nc=1、reg_max=16 → [1, 65, h, w]
               尺度 stride 依次为 8 / 16 / 32（80/40/20 @ 640 输入）
  nc: 类别数（本项目固定 1）
  reg_max: DFL 每个框坐标的离散 bin 数（YOLOv8 固定 16）

输出契约 D1（SDD §4.2）：
  boxes [N,5] = (cx, cy, w, h, conf)，坐标已还原到**模型输入尺度**（640 letterbox 域）；
  还原到原图尺度由调用方按 InputMeta.scale / letterbox 参数完成（适配层职责）。
"""

from __future__ import annotations

import numpy as np

REG_MAX = 16          # YOLOv8 DFL 离散 bin 数
STRIDES = (8, 16, 32)  # 三个检测尺度的 stride，顺序与 raw_outputs 一一对应


def _nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    """纯 numpy NMS，返回保留索引（按分数降序）。"""
    x1, y1, x2, y2 = boxes_xyxy[:, 0], boxes_xyxy[:, 1], boxes_xyxy[:, 2], boxes_xyxy[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.clip(xx2 - xx1, 0, None)
        h = np.clip(yy2 - yy1, 0, None)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def _dfl_projection(dists: np.ndarray) -> np.ndarray:
    """DFL 离散分布 → 期望距离。dists: [..., 4, REG_MAX] → [...]"""
    bins = np.arange(REG_MAX, dtype=np.float32)
    softmax = np.exp(dists - dists.max(axis=-1, keepdims=True))
    softmax = softmax / softmax.sum(axis=-1, keepdims=True)
    return (softmax * bins).sum(axis=-1)


def decode_boxes(
    raw_outputs: list[np.ndarray],
    nc: int = 1,
    conf_thres: float = 0.05,
    iou_thres: float = 0.5,
    max_det: int = 300,
) -> np.ndarray:
    """三个尺度的 raw 特征图 → [N,5] (cx, cy, w, h, conf)，输入尺度坐标。

    宽收 conf（默认 0.05）宽收 NMS，最终判决权交给级联门控
    （契约 D2/D3：不过滤、不判业务）。max_det 兜底防候选爆炸。
    """
    all_boxes: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []

    for feat, stride in zip(raw_outputs, STRIDES):
        feat = feat[0]  # [C, h, w]
        _, h, w = feat.shape
        reg_ch = 4 * REG_MAX

        # DFL 框距离（ltrb，特征图像素单位）
        dfl = feat[:reg_ch].reshape(4, REG_MAX, h * w).transpose(2, 0, 1)  # [hw,4,16]
        l, t, r, b = np.split(_dfl_projection(dfl), 4, axis=1)  # 各 [hw,1]

        # 网格中心
        gx = (np.arange(w, dtype=np.float32)[None, :].repeat(h, 0)).reshape(-1, 1)
        gy = (np.arange(h, dtype=np.float32)[:, None].repeat(w, 1)).reshape(-1, 1)
        gx = gx + 0.5
        gy = gy + 0.5

        # ltrb 距离（乘 stride 还原到输入像素域）→ xyxy
        x1 = (gx - l) * stride
        y1 = (gy - t) * stride
        x2 = (gx + r) * stride
        y2 = (gy + b) * stride
        xyxy = np.concatenate([x1, y1, x2, y2], axis=1)  # [hw,4]

        cls_logits = feat[reg_ch:].reshape(nc, h * w).T  # [hw,nc]，sigmoid 前的 logits
        conf = 1.0 / (1.0 + np.exp(-cls_logits.max(axis=1)))  # 真实 conf = sigmoid(logits)
        all_boxes.append(xyxy)
        all_scores.append(conf.astype(np.float32))

    boxes_xyxy = np.concatenate(all_boxes, axis=0)
    scores = np.concatenate(all_scores, axis=0)

    keep = scores > conf_thres
    boxes_xyxy, scores = boxes_xyxy[keep], scores[keep]
    if boxes_xyxy.shape[0] == 0:
        return np.zeros((0, 5), dtype=np.float32)

    keep_idx = _nms(boxes_xyxy, scores, iou_thres)[:max_det]
    boxes_xyxy, scores = boxes_xyxy[keep_idx], scores[keep_idx]

    cx = (boxes_xyxy[:, 0] + boxes_xyxy[:, 2]) / 2
    cy = (boxes_xyxy[:, 1] + boxes_xyxy[:, 3]) / 2
    bw = boxes_xyxy[:, 2] - boxes_xyxy[:, 0]
    bh = boxes_xyxy[:, 3] - boxes_xyxy[:, 1]
    return np.stack([cx, cy, bw, bh, scores], axis=1).astype(np.float32)


def letterbox(img: np.ndarray, new_size: int = 640, color: tuple = (114, 114, 114)):
    """等比缩放 + 灰边填充（A1 口径，与 ultralytics LetterBox 一致）。

    返回 (letterbox 后的图 [H,W,3], scale, pad_x, pad_y)，供坐标还原。
    """
    h, w = img.shape[:2]
    scale = min(new_size / h, new_size / w)
    nh, nw = round(h * scale), round(w * scale)
    resized = np.asarray(
        _resize(img, nw, nh), dtype=np.float32
    )
    pad_x, pad_y = (new_size - nw) // 2, (new_size - nh) // 2
    out = np.full((new_size, new_size, 3), color, dtype=np.float32)
    out[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    return out, scale, pad_x, pad_y


def _resize(img: np.ndarray, nw: int, nh: int) -> np.ndarray:
    """最近邻/双线性缩放的轻量封装；板上无 cv2 时用 numpy 线性插值近似。"""
    try:
        import cv2  # 开发机有则用 cv2，与 ultralytics 训练口径一致
        interp = cv2.INTER_LINEAR
        return cv2.resize(img, (nw, nh), interpolation=interp)
    except ImportError:
        ys = (np.arange(nh) * (img.shape[0] - 1) / max(nh - 1, 1)).astype(np.int32)
        xs = (np.arange(nw) * (img.shape[1] - 1) / max(nw - 1, 1)).astype(np.int32)
        return img[ys][:, xs]
