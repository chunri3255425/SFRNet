import torch
import torch.nn as nn
import torch.nn.functional as F


class FissionReorganizationModule(nn.Module):
    def __init__(self, in_channels=3, branch_out_channels=32):
        super(FissionReorganizationModule, self).__init__()

        # === 1. 基础设置 ===
        self.overlap = 16  # 重叠区域大小

        # === 2. 门控网络 (Gating Network / Router) ===
        # 替换了原先的 BlockClassifier，术语与论文完全对齐
        self.router = GatingNetwork(in_channels=in_channels, num_classes=4)

        # === 3. 四条专精专家分支 (Distinct Expert Branches) ===
        # 分路 0: 针对平坦/无摩尔纹区域/偏色校准 (1x1 卷积)
        self.branch_0 = nn.Sequential(
            nn.Conv2d(in_channels, branch_out_channels, kernel_size=1),
            nn.ReLU(),
            nn.Conv2d(branch_out_channels, branch_out_channels, kernel_size=3, padding=1),
            nn.ReLU()
        )

        # 分路 1: 针对密集的高频细密摩尔纹 (普通 3x3 卷积)
        self.branch_1 = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, branch_out_channels, kernel_size=3, padding=1),
            nn.ReLU()
        )

        # 分路 2: 针对密集的高频细密摩尔纹 (5x5 大卷积核)
        self.branch_2 = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, branch_out_channels, kernel_size=5, padding=2),
            nn.ReLU()
        )

        # 分路 3: 针对稀疏/低频大波纹 (空洞卷积扩大感受野)
        self.branch_3 = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, branch_out_channels, kernel_size=3, padding=2, dilation=2),
            nn.ReLU()
        )

        # 将分路放入 ModuleList 方便索引
        self.branches = nn.ModuleList([self.branch_0, self.branch_1, self.branch_2, self.branch_3])

        # === 4. 精炼层 (Refine Block) ===
        self.refine_block = nn.Sequential(
            nn.Conv2d(branch_out_channels, branch_out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(branch_out_channels),
            nn.LeakyReLU(0.2, inplace=True)
        )

    def process_block_hard_routing(self, block):
        """
        对单个 Block 进行分类并路由到指定分路，包含 STE 梯度修复
        """
        B_size = block.shape[0]

        # 1. 获取门控 Logits: [B, 4]
        logits = self.router(block)

        # 2. 计算软概率 (Softmax): [B, 4]
        probs = F.softmax(logits, dim=1)

        # 3. 获取硬路由的预测类别 (不可导): [B]
        preds = torch.argmax(probs, dim=1)

        # --- 核心修复：直通估计器 (Straight-Through Estimator, STE) ---
        # 提取选中类别的概率
        selected_probs = probs[torch.arange(B_size), preds]
        # 构造 STE 权重：前向传播时值为 1.0（不改变特征大小），反向传播时保留 probs 的梯度
        ste_weight = selected_probs - selected_probs.detach() + 1.0  # shape: [B]
        # -------------------------------------------------------------

        # 4. 初始化输出容器
        out_h, out_w = block.shape[2], block.shape[3]
        target_c = self.branches[0][2].out_channels
        output = torch.zeros(B_size, target_c, out_h, out_w, device=block.device, dtype=block.dtype)

        # 5. 根据类别路由 (Masking 策略)
        for i in range(4):
            idx = (preds == i).nonzero(as_tuple=True)[0]
            if len(idx) > 0:
                sub_input = block[idx]
                sub_output = self.branches[i](sub_input)

                # 将 STE 权重乘到输出上，连通计算图
                # ste_weight[idx] 的 shape 是 [len(idx)]，扩展为 [len(idx), 1, 1, 1] 以便广播
                branch_weight = ste_weight[idx].view(-1, 1, 1, 1)
                sub_output = sub_output * branch_weight

                output[idx] = sub_output.to(output.dtype)

        return output

    def forward(self, x):
        # x shape: [B, C, H, W]
        B, C, H, W = x.size()
        h_mid, w_mid = H // 2, W // 2
        p = self.overlap

        # 1. Padding
        x_pad = F.pad(x, (p, p, p, p), mode='reflect')

        # 2. 切分四个 Block
        tl = x_pad[:, :, 0: h_mid + 2 * p, 0: w_mid + 2 * p]
        tr = x_pad[:, :, 0: h_mid + 2 * p, w_mid: W + 2 * p]
        bl = x_pad[:, :, h_mid: H + 2 * p, 0: w_mid + 2 * p]
        br = x_pad[:, :, h_mid: H + 2 * p, w_mid: W + 2 * p]

        blocks = [tl, tr, bl, br]
        outputs = []

        # 3. 遍历每个位置的 Block 进行处理
        for block in blocks:
            out_block = self.process_block_hard_routing(block)
            outputs.append(out_block)

        # 4. 裁剪掉重叠区域 (Crop Overlaps)
        tl_out = outputs[0][:, :, p: p + h_mid, p: p + w_mid]
        tr_out = outputs[1][:, :, p: p + h_mid, p: p + (W - w_mid)]
        bl_out = outputs[2][:, :, p: p + (H - h_mid), p: p + w_mid]
        br_out = outputs[3][:, :, p: p + (H - h_mid), p: p + (W - w_mid)]

        # 5. 拼接 (Stitch)
        top_row = torch.cat([tl_out, tr_out], dim=3)
        bottom_row = torch.cat([bl_out, br_out], dim=3)
        full_map = torch.cat([top_row, bottom_row], dim=2)

        # 6. 精炼输出
        return self.refine_block(full_map)


class GatingNetwork(nn.Module):
    """
    门控网络 (Gating Network)，原 BlockClassifier。
    用于判断 Image Block 属于哪种特征类型，并分配给对应的 Expert。
    """

    def __init__(self, in_channels, num_classes=4):
        super(GatingNetwork, self).__init__()
        self.net = nn.Sequential(
            # 下采样提取特征
            nn.Conv2d(in_channels, 16, kernel_size=3, padding=1, stride=2),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, padding=1, stride=2),
            nn.ReLU(),
            # 全局池化，将空间维度变为 1x1
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            # 全连接层输出类别 logits
            nn.Linear(32, num_classes)
        )

    def forward(self, x):
        return self.net(x)