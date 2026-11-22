"""MoirePriorNet —— 权重全部解析构造的摩尔纹检测网络（零训练、零标签）。

状态（2026-11-22 代码整理）：**未接入任何调用路径**，`app/` 里没人 import 它，
也不在 `detectors/__init__.py` 的惰性导出里，所以它不影响板端的 torch-free
导入链。它是上一段 docstring 所述失败的直接回应，把无界比值改成了有界的
结构张量量；方向是对的，但最后落地的 Stage-2 替换件选了另一条：同类判据被
`build/export/rule.py` 烘成 DCT 规则图（每片一个标量），由
`app/adaptation/runtime/rule.py` 跑。本模块与 `classic`
这两份被淘汰的稿子合起来才是完整的
“为什么不是 FFT、为什么不是这个 prior”，所以只标注不删。

设计动机
--------
上一版纯 FFT 判据在真实数据上失败，失败模式很具体：
  1. "谱峰 ÷ 同半径本底"是无界比值，平滑图（拍天花板）本底趋零 → 分数爆表，
     实测天花板 63 dB > 真实摩尔纹 46 dB，方向都是反的；
  2. 频带取 0.02~0.30 cyc/px，而实测真实摩尔纹的谱峰在 0.0117 cyc/px（周期 85px），
     判据窗口根本没罩住目标。

所以这里不再调阈值，而是把摩尔纹的**物理定义**编成网络权重：摩尔纹是叠加在内容
上的**准单色、单一方向、长程相干**的干涉成分。四个量各自对应一组固定核：

  分频带   ← 拉普拉斯金字塔：二项式 5x5 模糊 + 逐级 2x 降采样，第 k 级带通中心
             周期 ≈ 6·2^k 像素（k=0..5 覆盖 6~192px，把实测 85px 拍频包在中间）
  方向性   ← 3x3 Sobel（固定）求带通信号的梯度
  长程相干 ← 9x9 均值窗（固定）累积结构张量 S11/S22/S12
  判别量   ← aniso = √((S11−S22)²+4S12²) / (S11+S22+ε)  ∈ [0,1]，**有界**，
             平滑图与噪声图都给不出高值，从根上消除"分母趋零爆炸"

  窄带性   ← 跨级 log 能量取 max − mean（用减法不用除法，同样避开 0/0）：
             文字/边缘是宽带，摩尔纹是窄带，这一项把两者分开
  能量门   ← 绝对 log 能量过下限，防止"什么都没有"被误判成"很相干"

输出为与输入同尺寸的 logits 图，直接满足引擎的滑窗 Hann 融合与 Decider 契约
（O1：未过 sigmoid 的原始分数），因此逻辑层、DTO、前端一行都不用改。

推理代价：全部是 depthwise 小核卷积 + 池化，512 tile 单片在 CPU 上为毫秒级，
相比 SFRNet 的 2965 ms/片有三个数量级的余量。
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

# 金字塔级数：第 k 级带通中心周期 ≈ 6·2^k px，6 级覆盖 6~192 px
LEVELS = 6
# 相干累积窗（结构张量平滑核）。9 px 在 k=3 级对应原生 72 px ≈ 一个拍频周期
COH_WIN = 9
# 各判别量的固定权重与偏置：由物理量纲与实测分布定标，不是学出来的
W_ANISO = 2.60      # 方向相干度（主判据）
W_NARROW = 1.80     # 跨频带窄带性
W_GATE = 0.90       # 绝对能量门（斜率）
GATE_FLOOR = 2.0    # 能量门下界（log 能量低于此直接判无纹）
BIAS = -4.20        # 使"无纹"落在负 logits、"有纹"落在正 logits
EPS = 1e-4


def _binomial5() -> torch.Tensor:
    k = torch.tensor([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0
    return (k[:, None] * k[None, :]).contiguous()


def _sobel() -> torch.Tensor:
    s = torch.tensor([[1.0, 0.0, -1.0], [2.0, 0.0, -2.0], [1.0, 0.0, -1.0]]) / 8.0
    t = s.t().contiguous()
    return torch.stack([s, t])          # [2,3,3]：gx, gy


class MoirePriorNet(nn.Module):
    """固定权重摩尔纹检测器。forward: [1,3,H,W] (0~1, RGB) → [1,1,H,W] raw logits."""

    def __init__(self, levels: int = LEVELS, coh_win: int = COH_WIN):
        super().__init__()
        self.levels, self.coh_win = levels, coh_win
        ch = 3                            # Y / Cb / Cr 三通道的处理是 depthwise 并行的

        blur = _binomial5()
        self.register_buffer("w_blur", blur[None, None].repeat(ch, 1, 1, 1).contiguous())
        sob = _sobel()
        # 分组卷积权重 [out=6, in/group=1, 3, 3]，输出序为 (gx0,gy0,gx1,gy1,gx2,gy2)
        self.register_buffer("w_sob", sob[:, None].repeat(ch, 1, 1, 1).contiguous())
        box = torch.ones(coh_win, coh_win) / (coh_win * coh_win)
        self.register_buffer("w_box", box[None, None].repeat(ch, 1, 1, 1).contiguous())
        # rgb → YCbCr（BT.601，行和为 1，色度零均值）
        ycc = torch.tensor([[0.299, 0.587, 0.114],
                            [-0.169, -0.331, 0.500],
                            [0.500, -0.419, -0.081]])
        self.register_buffer("w_ycc", ycc[:, :, None, None].contiguous())
        self.register_buffer("b_ycc", torch.tensor([0.0, 0.5, 0.5]))

    # ── 金字塔：逐级返回带通残差（第 k 级分辨率 = 原图 / 2^k）──
    def _bands(self, x: torch.Tensor) -> list[torch.Tensor]:
        out, cur = [], x
        for _ in range(self.levels):
            b = F.conv2d(cur, self.w_blur, padding=2, groups=cur.shape[1])
            d = F.avg_pool2d(b, 2)
            up = F.interpolate(d, scale_factor=2, mode="bilinear", align_corners=False)
            if up.shape[-2:] != b.shape[-2:]:
                up = F.pad(up, (0, b.shape[-1] - up.shape[-1], 0, b.shape[-2] - up.shape[-2]))
            out.append(b - up)
            cur = d
        return out

    def _features(self, band: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """返回该级的 (方向相干度, log 能量)，分辨率与该级带通一致。"""
        g = F.conv2d(band, self.w_sob, padding=1, groups=band.shape[1])   # [N,6,h,w]
        gx, gy = g[:, 0::2], g[:, 1::2]
        s11 = F.conv2d(gx * gx, self.w_box, padding=self.coh_win // 2, groups=band.shape[1])
        s22 = F.conv2d(gy * gy, self.w_box, padding=self.coh_win // 2, groups=band.shape[1])
        s12 = F.conv2d(gx * gy, self.w_box, padding=self.coh_win // 2, groups=band.shape[1])
        tr = s11 + s22
        aniso = torch.sqrt(torch.clamp((s11 - s22) ** 2 + 4 * s12 * s12, min=0.0)) \
            / (tr + EPS)
        return aniso, torch.log(tr.mean(dim=1, keepdim=True) + EPS)

    def forward(self, tile: torch.Tensor) -> torch.Tensor:
        h, w = tile.shape[-2:]
        ycc = F.conv2d(tile, self.w_ycc, self.b_ycc)          # [1,3,H,W]
        bands = self._bands(ycc)
        feats = [self._features(b) for b in bands]

        aniso = [F.interpolate(a, size=(h, w), mode="bilinear", align_corners=False)
                 for a, _ in feats]
        loge = [F.interpolate(e, size=(h, w), mode="bilinear", align_corners=False)
                for _, e in feats]
        stack_a = torch.cat(aniso, dim=1)                     # [1,L,h,w]
        stack_e = torch.cat(loge, dim=1)

        # 窄带性：跨级 log 能量的 max − mean（减法形式，等价于比值但不会 0/0）
        narrow = stack_e.amax(dim=1, keepdim=True) - stack_e.mean(dim=1, keepdim=True)
        # 能量门：最强级的绝对能量，低于地板直接压成 0
        gate = torch.clamp(stack_e.amax(dim=1, keepdim=True) - GATE_FLOOR, min=0.0)
        # 方向相干度取各级最大值
        coh = stack_a.amax(dim=1, keepdim=True)

        logits = W_ANISO * coh + W_NARROW * narrow + W_GATE * gate + BIAS
        return logits

    # DetectorCore 协议要求的方法在 nn.Module 里天然存在（eval/to/parameters）


def build() -> MoirePriorNet:
    """注册表工厂入口：返回权重已解析构造好的实例（无需外部权重文件也存在）。"""
    return MoirePriorNet()


def describe() -> str:
    n = sum(p.numel() for p in MoirePriorNet().parameters())
    lines = [
        f"可学习参数张量个数 = {n}（本网络全部为 buffer，故为 0）",
        f"级数={LEVELS} 相干窗={COH_WIN} 权重 W_aniso={W_ANISO} W_narrow={W_NARROW} "
        f"W_gate={W_GATE} 门地板={GATE_FLOOR} 偏置={BIAS}",
        f"带通中心周期 ≈ {[round(6 * 2 ** k) for k in range(LEVELS)]} px",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    torch.set_grad_enabled(False)
    m = MoirePriorNet().eval()
    x = torch.rand(1, 3, 512, 512)
    y = m(x)
    print(describe())
    print(f"输入 {tuple(x.shape)} → logits {tuple(y.shape)}  范围 [{y.min():.2f}, {y.max():.2f}]")
