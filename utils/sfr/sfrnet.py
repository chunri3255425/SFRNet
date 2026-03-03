import torch
import torch.nn as nn
import torch.nn.functional as F
from utils.sfr.FRM import FissionReorganizationModule
from utils.sfr.safm import SAFM

class Stem_Block(nn.Module):
    def __init__(self, in_c, out_c, stride):
        super().__init__()
        self.c1 = nn.Sequential(
            nn.Conv2d(in_c, out_c, kernel_size=3, stride=stride, padding=1),
            nn.BatchNorm2d(out_c),
            nn.ReLU(),
            nn.Conv2d(out_c, out_c, kernel_size=3, padding=1),
        )
        self.c2 = nn.Sequential(
            nn.Conv2d(in_c, out_c, kernel_size=1, stride=stride, padding=0),
            nn.BatchNorm2d(out_c),
        )
        self.safm = SAFM(in_channel=out_c, inter_num=16)

    def forward(self, inputs):
        x = self.c1(inputs)
        s = self.c2(inputs)
        #return (s+x)
        return self.safm(x + s)

class ResNet_Block(nn.Module):
    def __init__(self, in_c, out_c, stride):
        super().__init__()
        self.c1 = nn.Sequential(
            nn.BatchNorm2d(in_c),
            nn.ReLU(),
            nn.Conv2d(in_c, out_c, kernel_size=3, padding=1, stride=stride),
            nn.BatchNorm2d(out_c),
            nn.ReLU(),
            nn.Conv2d(out_c, out_c, kernel_size=3, padding=1)
        )
        self.c2 = nn.Sequential(
            nn.Conv2d(in_c, out_c, kernel_size=1, stride=stride, padding=0),
            nn.BatchNorm2d(out_c),
        )
        self.safm = SAFM(in_channel=out_c, inter_num=32)

    def forward(self, inputs):
        x = self.c1(inputs)
        s = self.c2(inputs)
        #return (s + x)
        return self.safm(x + s)


class ASPP(nn.Module):
    def __init__(self, in_c, out_c, rate=[1, 6, 12, 18]):
        super().__init__()
        self.c1 = nn.Sequential(nn.Conv2d(in_c, out_c, 3, dilation=rate[0], padding=rate[0]), nn.BatchNorm2d(out_c))
        self.c2 = nn.Sequential(nn.Conv2d(in_c, out_c, 3, dilation=rate[1], padding=rate[1]), nn.BatchNorm2d(out_c))
        self.c3 = nn.Sequential(nn.Conv2d(in_c, out_c, 3, dilation=rate[2], padding=rate[2]), nn.BatchNorm2d(out_c))
        self.c4 = nn.Sequential(nn.Conv2d(in_c, out_c, 3, dilation=rate[3], padding=rate[3]), nn.BatchNorm2d(out_c))
        self.c5 = nn.Conv2d(out_c, out_c, kernel_size=1)

    def forward(self, inputs):
        return self.c5(self.c1(inputs) + self.c2(inputs) + self.c3(inputs) + self.c4(inputs))


class Attention_Block(nn.Module):
    def __init__(self, in_c):
        super().__init__()
        # in_c[0] = Gate (low res, deep feature), in_c[1] = Skip (high res, shallow feature)
        out_c = in_c[1]

        # 修复: 移除 MaxPool。Gate 本身就比 Skip 小，我们需要对齐它们。
        self.g_conv = nn.Sequential(
            nn.BatchNorm2d(in_c[0]),
            nn.ReLU(),
            nn.Conv2d(in_c[0], out_c, kernel_size=3, padding=1)
        )

        self.x_conv = nn.Sequential(
            nn.BatchNorm2d(in_c[1]),
            nn.ReLU(),
            nn.Conv2d(in_c[1], out_c, kernel_size=3, padding=1),
        )

        self.gc_conv = nn.Sequential(
            nn.BatchNorm2d(out_c),
            nn.ReLU(),
            nn.Conv2d(out_c, 1, kernel_size=1),  # Attention map 通常输出 1 通道权重
            nn.Sigmoid()
        )

    def forward(self, g, x):
        # g: Gate, x: Skip
        g_feat = self.g_conv(g)
        x_feat = self.x_conv(x)

        # 必须将 Gate 上采样到 Skip 的大小才能相加
        if g_feat.shape[2:] != x_feat.shape[2:]:
            g_feat = F.interpolate(g_feat, size=x_feat.shape[2:], mode='bilinear', align_corners=True)

        attn = self.gc_conv(g_feat + x_feat)
        return x * attn  # 加权后的 Skip


class Decoder_Block(nn.Module):
    def __init__(self, in_c, out_c):
        super().__init__()
        # in_c: [Gate_channels, Skip_channels]
        # 注意：这里参数我反转了一下以符合直觉，调用时需注意
        self.a1 = Attention_Block(in_c)

        self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)

        self.r1 = ResNet_Block(in_c[0] + in_c[1], out_c, stride=1)
        self.safm = SAFM(in_channel=out_c, inter_num=16)

    def forward(self, g, x):
        # 1. 计算 Attention (利用 g 指导 x)
        x_attn = self.a1(g, x)

        # 2. 上采样 Gate (Deep feature)
        g_up = self.up(g)

        # 3. 尺寸对齐 (防止奇数尺寸导致的 1px 误差)
        if g_up.shape[2:] != x_attn.shape[2:]:
            g_up = F.interpolate(g_up, size=x_attn.shape[2:], mode='bilinear', align_corners=True)

        # 4. 拼接
        d = torch.cat([g_up, x_attn], dim=1)

        d = self.r1(d)
        d = self.safm(d)
        return d


class build_SFR(nn.Module):
    def __init__(self):
        super().__init__()

        # 主干网络 (ResNet-like Encoder)
        self.c1 = Stem_Block(3, 16, stride=1)  # Out: 16
        self.c2 = ResNet_Block(16, 32, stride=2)  # Out: 32
        self.c3 = ResNet_Block(32, 64, stride=2)  # Out: 64
        self.c4 = ResNet_Block(64, 128, stride=2)  # Out: 128

        # 瓶颈层
        self.b1 = ASPP(128, 256)  # Out: 256

        # 旁支网络 (FRM)
        self.fission_module = FissionReorganizationModule(in_channels=3, branch_out_channels=32)

        # 融合适配器: 将 FRM (32ch) 转换维度以融合进 Bottleneck (256ch)
        self.fusion_conv = nn.Sequential(
            nn.Conv2d(32, 256, kernel_size=1),
            nn.BatchNorm2d(256),
            nn.ReLU()
        )

        # 解码器 (Decoder)
        # 参数定义: [Gate_Ch, Skip_Ch], Out_Ch
        self.d1 = Decoder_Block([256, 64], 128)
        self.d2 = Decoder_Block([128, 32], 64)
        self.d3 = Decoder_Block([64, 16], 32)

        self.aspp_out = ASPP(32, 16)
        self.output = nn.Conv2d(16, 1, kernel_size=1)

    def forward(self, inputs):
        # --- 1. 并行计算 ---

        # A路: 主干网络提取语义特征
        c1 = self.c1(inputs)  # [B, 16, H, W]
        c2 = self.c2(c1)  # [B, 32, H/2, W/2]
        c3 = self.c3(c2)  # [B, 64, H/4, W/4]
        c4 = self.c4(c3)  # [B, 128, H/8, W/8]

        center = self.b1(c4)  # [B, 256, H/8, W/8]

        #B路: FRM 提取频域/摩尔纹特征 (输入也是原始 inputs)
        frm_out = self.fission_module(inputs)  # [B, 32, H, W]

        frm_down = F.interpolate(frm_out, size=center.shape[2:], mode='bilinear', align_corners=True)
        fusion_feat = self.fusion_conv(frm_down)

        center = center + fusion_feat  # 融合 (Addition)

        d1 = self.d1(g=center, x=c3)  # Out: 128
        d2 = self.d2(g=d1, x=c2)  # Out: 64
        d3 = self.d3(g=d2, x=c1)  # Out: 32

        output = self.aspp_out(d3)
        output = self.output(output)

        return output