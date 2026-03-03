import os
import torch
import torch.nn as nn
import numpy as np
from utils.metrics import jac_score, precision,recall, F2
from torch.utils.data import DataLoader
from torch.amp import autocast, GradScaler
from tqdm import tqdm
from utils.data_loding import MoireTargetDataset
from utils.sfr.sfrnet import build_SFR


def train_one_epoch(model, loader, optimizer, criterion, scaler, device):
    model.train()
    running_loss = 0.0

    pbar = tqdm(loader, desc='Train', leave=False)
    for moire, target in pbar:
        moire = moire.to(device, non_blocking=True)
        target = target.to(device, non_blocking=True)

        optimizer.zero_grad(set_to_none=True)

        with autocast(device_type='cuda:1', enabled=(device.type == 'cuda')):
            output = model(moire)
            loss = criterion(output, target)

        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

        running_loss += loss.item()
        pbar.set_postfix(loss=f'{loss.item():.4f}')

    return running_loss / len(loader)



class CombinedLoss(nn.Module):
    def __init__(self, bce_weight=0.5):
        super().__init__()
        self.bce = nn.BCEWithLogitsLoss()
        self.bce_weight = bce_weight

    def forward(self, logits, target):
        # 1. BCE Loss
        bce_loss = self.bce(logits, target)

        # 2. Dice Loss
        smooth = 1e-6
        probs = torch.sigmoid(logits)
        # 展平处理
        probs_flat = probs.view(-1)
        target_flat = target.view(-1)

        intersection = (probs_flat * target_flat).sum()
        dice_score = (2. * intersection + smooth) / (probs_flat.sum() + target_flat.sum() + smooth)
        dice_loss = 1 - dice_score

        return self.bce_weight * bce_loss + (1 - self.bce_weight) * dice_loss

def compute_jaccard(pred, target, eps=1e-6):
    pred = pred.bool()
    target = target.bool()

    intersection = (pred & target).sum(dim=(1, 2, 3))
    union = (pred | target).sum(dim=(1, 2, 3))

    jaccard = (intersection + eps) / (union + eps)
    return jaccard.mean().item()


def predict_sliding_window(model, image, tile_size=512, overlap=1 / 3, num_classes=1):

    b, c, h, w = image.shape
    stride = int(tile_size * (1 - overlap))

    # 最终的大图概率图
    full_probs = torch.zeros((b, num_classes, h, w), device=image.device)
    # 计数图 (用于记录每个像素被预测了多少次，用于求平均)
    count_map = torch.zeros((b, num_classes, h, w), device=image.device)

    for y in range(0, h, stride):
        for x in range(0, w, stride):
            # 计算当前窗口的坐标 (确保不越界)
            y1 = min(y, h - tile_size)
            x1 = min(x, w - tile_size)
            y2 = y1 + tile_size
            x2 = x1 + tile_size

            # 只有当图片比 tile_size 还小的时候，特殊处理
            if y1 < 0: y1 = 0; y2 = h
            if x1 < 0: x1 = 0; x2 = w

            # 切片
            img_tile = image[:, :, y1:y2, x1:x2]

            # 推理
            with torch.no_grad():
                # 注意：这里直接输出 logits
                tile_logits = model(img_tile)

                # 累加结果
            full_probs[:, :, y1:y2, x1:x2] += tile_logits
            count_map[:, :, y1:y2, x1:x2] += 1

            # 如果已经到达右边界，换行
            if x2 == w: break
        # 如果已经到达下边界，结束
        if y2 == h: break

    # 取平均 (重叠区域取多次预测的平均值，效果更平滑)
    full_probs /= count_map
    return full_probs


def validate_one_epoch(model, loader, criterion, device, threshold=0.5):
    model.eval()
    running_loss = 0.0

    total_jaccard = 0.0
    total_f1 = 0.0
    total_recall = 0.0
    total_precision = 0.0
    total_valid = 0

    # === 设置滑动窗口大小 ===
    # 建议设为 512 或 256。太小速度慢，太大显存炸。
    TILE_SIZE = 512

    pbar = tqdm(loader, desc='Val  ', leave=False)
    with torch.no_grad():
        for moire, target in pbar:
            moire = moire.to(device, non_blocking=True)
            target = target.to(device, non_blocking=True)

            # === 修改点：使用滑动窗口推理 ===
            # 原来: logits = model(moire)
            # 现在:
            if moire.shape[2] > TILE_SIZE or moire.shape[3] > TILE_SIZE:
                logits = predict_sliding_window(model, moire, tile_size=TILE_SIZE, overlap=0.25)
            else:
                logits = model(moire)  # 小图直接跑

            loss = criterion(logits, target)
            running_loss += loss.item()

            prob = torch.sigmoid(logits)
            pred = (prob > threshold).float()

            # 指标计算不变
            batch_jaccard = jac_score(target, pred)
            batch_f1 = F2(target, pred)
            batch_recall = recall(target, pred)
            batch_precision = precision(target, pred)

            total_jaccard += batch_jaccard
            total_f1 += batch_f1
            total_recall += batch_recall
            total_precision += batch_precision
            total_valid += moire.size(0)  # 验证集 batch_size 必须为 1

            pbar.set_postfix(loss=f'{loss.item():.4f}')

    avg_loss = running_loss / len(loader)
    jaccard = total_jaccard / total_valid
    f1 = total_f1 / total_valid
    rca = total_recall / total_valid
    pre = total_precision / total_valid

    return avg_loss, jaccard, f1, rca, pre



def main():
    TRAIN_ROOT = './data/train_data'
    VAL_ROOT = './data/test_data'

    BATCH_SIZE = 6
    NUM_EPOCHS = 100
    LR = 3e-5
    NUM_WORKERS = 8
    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    SAVE_DIR = './checkpoints/SFR'
    os.makedirs(SAVE_DIR, exist_ok=True)

    train_dataset = MoireTargetDataset(TRAIN_ROOT, crop_size=512, crop_type='random')
    #train_dataset = MoireTargetDataset(TRAIN_ROOT, crop_type='random')
    val_dataset = MoireTargetDataset(VAL_ROOT, is_train=False)

    train_loader = DataLoader(
        train_dataset, batch_size=BATCH_SIZE,
        shuffle=True, num_workers=NUM_WORKERS, pin_memory=True, drop_last=True
    )

    val_loader = DataLoader(
        val_dataset, batch_size=1,
        shuffle=False, num_workers=4, pin_memory=True
    )


    model = build_SFR().to(DEVICE)

    criterion = CombinedLoss(bce_weight=0.3)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'max', patience=5, factor=0.5)
    scaler = GradScaler(enabled=(DEVICE.type == 'cuda'))

    best_jaccard = float('-inf')

    for epoch in range(1, NUM_EPOCHS + 1):
        train_loss = train_one_epoch(
            model, train_loader, optimizer, criterion, scaler, DEVICE
        )

        val_loss, val_jaccard, val_f1, val_recall, val_precision = validate_one_epoch(
            model, val_loader, criterion, DEVICE
        )

        print(f'Epoch [{epoch:03d}/{NUM_EPOCHS}] '
              f'| Train Loss: {train_loss:.6f} '
              f'| Val Loss: {val_loss:.6f} '
              f'| IoU: {val_jaccard:.4f} '
              f'| F1: {val_f1:.4f} '
              f'| Recall: {val_recall:.4f} '
              f'| Precision: {val_precision:.4f}')

        # 保存模型
        torch.save(
            model.state_dict(),
            os.path.join(SAVE_DIR, f'SFR_epoch_{epoch:03d}.pth')
        )

        if val_jaccard > best_jaccard:
            best_jaccard = val_jaccard
            torch.save(
                model.state_dict(),
                os.path.join(SAVE_DIR, 'SFR_best.pth')
            )
            print('  -> Best model updated.')
        scheduler.step(val_jaccard)  # 监测 IOU，如果 5 个 epoch 不涨，就让学习率减半

if __name__ == '__main__':
    main()
