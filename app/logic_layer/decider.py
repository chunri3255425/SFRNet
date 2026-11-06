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
    prob_mean: float = 0.0        # 仅 want_mean=True 时有效，见 decide()


class Decider:
    """双门限判定器。threshold 过滤置信度，coverage_threshold 过滤规模。"""

    def __init__(self, threshold: float = 0.5, coverage_threshold: float = 0.01):
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold 必须在 (0,1)：{threshold}")
        if not 0.0 < coverage_threshold < 1.0:
            raise ValueError(f"coverage_threshold 必须在 (0,1)：{coverage_threshold}")
        self.threshold = threshold
        self.coverage_threshold = coverage_threshold

    def decide(self, logits: np.ndarray, want_mean: bool = False) -> Decision:
        """判定一帧。C-06：sigmoid 全流程只在此处做一次，之后所有归约复用 prob。

        Args:
            logits: 融合后的整图 raw logits
            want_mean: 是否额外计算 mean_prob。mean 是又一趟全数组扫描
                       （4K 实测 ~12ms，占单帧 0.3%），只有确实要构造广播
                       DTO 时才付，所以由调用方按「有没有人订阅」传入。
        """
        prob = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))
        mask = prob > self.threshold
        coverage = float(mask.mean())
        return Decision(
            has_moire=coverage > self.coverage_threshold,   # 严格大于（V-10）
            coverage=coverage,
            prob_max=float(prob.max()),
            prob_mean=float(prob.mean()) if want_mean else 0.0,
        )
