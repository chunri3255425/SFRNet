"""纯代码摩尔纹检测（Stage-2 SFRNet 替换的第一版）。

状态（2026-11-22 代码整理）：**未接入任何调用路径**，`app/` 里没人 import 它。
现行 Stage-2 替换件是 `build/export/rule.py` 烘出的 DCT 规则图，由
`app/adaptation/runtime/rule.py` 执行。本模块的整图判据当时实测
AUC 0.73 / 0.62（摩尔纹 vs 正常几乎分不开）；被淘汰的具体原因记在
`app/model_layer/detectors/prior.py` 的 docstring 开头。文件留着不删，
是为了让“为什么此路不通”留一份能跑的原件（复算脚本 `.scratch/eval_classic.py`，
过程件不入库）。

原理
----
摩尔纹是相机传感栅格与屏幕像素栅格相互干涉产生的**准周期条纹**。它在频域的
特征是确定的：能量集中在少数**孤立、窄带、高对比**的峰上，且峰成对出现在过原
点的直线上（实信号的共轭对称）。

对照两类不该报警的东西：
  · 自然纹理（天花板、百叶窗、织物）—— 谱近似 1/f 宽带连续衰减，没有孤立尖峰；
  · 数字截图（纯文本、平涂色块）—— 能量集中在轴向栅格与文字边缘，径向分布平滑。

所以判据取「同一半径邻域内的峰值对中位数的比」，即谱尖峰度（peak-to-median），
而不是总能量或对比度。这样亮度、噪声、内容复杂度都不影响它 —— 而这正是被替换掉
的模型犯的错（实测其概率≈由平均亮度决定，误报率 100%）。

输出契约
--------
`detect()` 返回整图概率图 prob ∈ [0,1]。引擎下游的 Decider 仍按 C-06 做唯一一次
sigmoid，所以这里用 `prob_to_logits()` 把概率可逆地映射成 logits
（sigmoid(logit(p)) == p，数值上无损），从而**不改 Decider、不改 DTO、不改前端**
就能替换掉整个 Stage-2。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

EPS = 1e-12


@dataclass
class ClassicParams:
    patch: int = 256          # 分析窗边长（2 的幂，FFT 快）
    stride: int = 128         # 窗步进；<=patch 保证重叠，条纹跨窗不漏
    f_lo: float = 0.02        # 频段下沿（cycles/px）：再低是照度渐变，不是条纹
    f_hi: float = 0.30        # 频段上沿：再高是像素栅格与噪声，屏幕固有
    n_bins: int = 24          # 径向分箱数，用于估计"同半径本底"
    lo: float = 8.0           # 谱尖峰度 dB 下沿 → prob 0
    hi: float = 26.0          # 谱尖峰度 dB 上沿 → prob 1
    use_chroma: bool = True   # 彩色摩尔纹常见于色度通道，亮度弱时补一路
    top_k: int = 3            # 全图分数取最高的 k 个窗均值（抗单窗噪声）


class ClassicMoireDetector:
    """无模型摩尔纹检测器。线程安全：实例内只有只读的预计算掩码。"""

    def __init__(self, params: ClassicParams | None = None):
        p = self.p = params or ClassicParams()
        n = p.patch
        if n & (n - 1):
            raise ValueError(f"patch 必须是 2 的幂以便快速 FFT：{n}")

        w1 = np.blackman(n).astype(np.float32)
        self._win = np.outer(w1, w1)

        f = np.fft.fftfreq(n).astype(np.float32)
        fy, fx = np.meshgrid(f, f, indexing="ij")
        r = np.sqrt(fy * fy + fx * fx)
        edges = np.linspace(p.f_lo, p.f_hi, p.n_bins + 1)
        bid = np.clip(np.digitize(r, edges) - 1, 0, p.n_bins - 1)
        # 每个径向箱的像素索引预先算好，逐窗只剩 24 次 take
        self._bins = [np.flatnonzero(bid.ravel() == b) for b in range(p.n_bins)]
        self._n = n

    # ── 单窗谱尖峰度 ──────────────────────────
    def _saliency_db(self, patch_u8: np.ndarray) -> float:
        """返回该窗的谱尖峰度（dB）。无有效本底时返回 0（=不报警）。"""
        g = patch_u8.astype(np.float32)
        if g.ndim == 3:                      # BGR → 亮度
            g = g[:, :, 0] * 0.114 + g[:, :, 1] * 0.587 + g[:, :, 2] * 0.299
        g = (g - g.mean()) * self._win
        if g.std() < 1e-3:                  # 纯色窗：谱是数值噪声，直接跳过
            return 0.0
        F = np.fft.fft2(g)
        P = (F.real * F.real + F.imag * F.imag).ravel()
        best = 0.0
        for idx in self._bins:
            if idx.size < 16:
                continue
            v = P[idx]
            med = np.median(v)
            if med <= EPS:
                continue
            s = 10.0 * np.log10((v.max() + EPS) / (med + EPS))
            if s > best:
                best = s
        return float(best)

    def _score(self, img_u8: np.ndarray) -> tuple[np.ndarray, int]:
        """滑窗打分，返回 (低分辨率分数图 dB, 窗数)。"""
        h, w = img_u8.shape[:2]
        p, st = self.p.patch, self.p.stride
        if h < p or w < p:                  # 小图：缩放到能容纳一个窗
            s = min(h / p, w / p)
            img_u8 = cv2.resize(img_u8, (max(p, int(w * s)), max(p, int(h * s))),
                                interpolation=cv2.INTER_LINEAR)
            h, w = img_u8.shape[:2]
        rows = list(range(0, h - p + 1, st)) or [0]
        cols = list(range(0, w - p + 1, st)) or [0]
        if rows[-1] + p < h:
            rows.append(h - p)
        if cols[-1] + p < w:
            cols.append(w - p)
        gray = cv2.cvtColor(img_u8, cv2.COLOR_BGR2GRAY) if img_u8.ndim == 3 else img_u8
        chroma = (img_u8[:, :, 2].astype(np.int16) - img_u8[:, :, 0]
                  if (self.p.use_chroma and img_u8.ndim == 3) else None)
        grid = np.zeros((len(rows), len(cols)), dtype=np.float32)
        for i, y in enumerate(rows):
            for j, x in enumerate(cols):
                s = self._saliency_db(gray[y:y + p, x:x + p])
                if chroma is not None and s < self.p.hi:
                    c = self._saliency_db(chroma[y:y + p, x:x + p].astype(np.float32))
                    s = max(s, c)
                grid[i, j] = s
        return grid, len(rows) * len(cols)

    # ── 对外主入口 ────────────────────────────
    def detect(self, img_u8: np.ndarray) -> tuple[np.ndarray, dict]:
        """整图检测。返回 (prob 图 [0,1] 与原图同尺寸, 元信息)。"""
        grid, tiles = self._score(img_u8)
        flat = np.sort(grid.ravel())[::-1]
        k = min(self.p.top_k, flat.size)
        score = float(flat[:k].mean())                       # 全图代表分数
        prob = np.clip((grid - self.p.lo) / max(self.p.hi - self.p.lo, 1e-6), 0.0, 1.0)
        h, w = img_u8.shape[:2]
        if prob.shape[0] < 2 or prob.shape[1] < 2:           # resize 需 >=2x2
            prob = np.repeat(np.repeat(prob, 2, 0), 2, 1)
        prob_full = cv2.resize(prob, (w, h), interpolation=cv2.INTER_LINEAR)
        return prob_full.astype(np.float32), {
            "tiles": tiles,
            "score_db": score,
            "grid_shape": grid.shape,
        }


def prob_to_logits(prob: np.ndarray) -> np.ndarray:
    """概率 → logits，供 Decider 做它那次唯一的 sigmoid。

    截到 [1e-4, 1-1e-4] 是为了 logit 有限；对应的 logits 范围 ±9.2，判定行为
    与截断前一致（threshold 默认 0.5 落在中间，不受端点压缩影响）。
    """
    p = np.clip(prob.astype(np.float64), 1e-4, 1.0 - 1e-4)
    return np.log(p / (1.0 - p))
