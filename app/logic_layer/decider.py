"""Decider — 双门限判定（SDD §4.3.4 / CONTROL_LOGIC §5.1）。

三步判定，唯一正确顺序：
  ① logits → prob       prob = sigmoid(logits)          ← 全流程唯一一次
  ② prob → mask         mask = prob > threshold         ← 严格大于
  ③ mask → bool         coverage = mask.mean()
                        has_moire = coverage > coverage_threshold  ← 严格大于
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Decision:
    has_moire: bool
    coverage: float
    prob_max: float


class Decider:
    """双门限判定器。threshold 过滤置信度，coverage_threshold 过滤规模。"""

    def __init__(self, threshold: float = 0.5, coverage_threshold: float = 0.01):
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold 必须在 (0,1)：{threshold}")
        if not 0.0 < coverage_threshold < 1.0:
            raise ValueError(f"coverage_threshold 必须在 (0,1)：{coverage_threshold}")
        self.threshold = threshold
        self.coverage_threshold = coverage_threshold

    def decide(self, logits: np.ndarray) -> Decision:
        prob = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))
        mask = prob > self.threshold
        coverage = float(mask.mean())
        return Decision(
            has_moire=coverage > self.coverage_threshold,   # 严格大于（V-10）
            coverage=coverage,
            prob_max=float(prob.max()),
        )
