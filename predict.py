#测试模型，把已经训练好的模型进行测试
import os
import torch
import yaml
import torchvision.transforms as transforms
from PIL import Image
import numpy as np
from upstream.sfr.sfrnet import build_SFR


def main():
    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    MODEL_PATH = r'./checkpoints/ins/SFR_best.pth'
    TEST_DIR = r'./data/test_data1/moire'
    SAVE_DIR = r'./data/test_data/result_INS1'
    os.makedirs(SAVE_DIR, exist_ok=True)


    model = build_SFR().to(DEVICE)
    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.eval()

    TILE_SIZE = 512  # 每个小块的大小（必须与训练时一致）
    STRIDE = 256  # 步长。Tile_size - Stride = 重叠部分（这里重叠64像素，用于平滑边缘）

    transform = transforms.ToTensor()

    with torch.no_grad():
        for name in os.listdir(TEST_DIR):
            if not name.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                continue

            img_path = os.path.join(TEST_DIR, name)
            img = Image.open(img_path).convert('RGB')
            w, h = img.size

            # 转为 Tensor: [1, 3, H, W]
            img_tensor = transform(img).unsqueeze(0).to(DEVICE)

            # 初始化结果画布和计数地图
            full_mask = torch.zeros((1, 1, h, w)).to(DEVICE)
            count_map = torch.zeros((1, 1, h, w)).to(DEVICE)

            # --- 生成坐标列表（核心算法：处理未覆盖区域） ---
            def get_coords(full_size, tile_size, stride):
                coords = []
                pos = 0
                while pos + tile_size <= full_size:
                    coords.append(pos)
                    pos += stride
                # 如果最后一块没到头，强制加上最后一块，使其紧贴边缘回退
                if coords[-1] + tile_size < full_size:
                    coords.append(full_size - tile_size)
                return coords

            # 如果原图比 TILE_SIZE 还小，直接补齐坐标
            y_coords = get_coords(h, TILE_SIZE, STRIDE) if h >= TILE_SIZE else [0]
            x_coords = get_coords(w, TILE_SIZE, STRIDE) if w >= TILE_SIZE else [0]

            # 如果图片尺寸小于 TILE_SIZE，需要先 Padding（补零）到 512
            if h < TILE_SIZE or w < TILE_SIZE:
                pad_h = max(0, TILE_SIZE - h)
                pad_w = max(0, TILE_SIZE - w)
                img_tensor = torch.nn.functional.pad(img_tensor, (0, pad_w, 0, pad_h))

            # --- 开始滑动窗口预测 ---
            for y in y_coords:
                for x in x_coords:
                    # 裁剪图像块 [1, 3, 512, 512]
                    tile = img_tensor[:, :, y:y + TILE_SIZE, x:x + TILE_SIZE]

                    # 模型推理
                    logits = model(tile)
                    prob = torch.sigmoid(logits)  # 映射到 [0, 1]

                    # 将预测结果累加回画布
                    # 注意：如果之前做了 Padding，累加时要小心对应的尺寸
                    full_mask[:, :, y:y + TILE_SIZE, x:x + TILE_SIZE] += prob
                    count_map[:, :, y:y + TILE_SIZE, x:x + TILE_SIZE] += 1

            # 计算平均值（重叠部分除以次数，消除接缝）
            final_prob = full_mask / count_map
            # 二值化
            mask = (final_prob > 0.5).float()

            # 转回 PIL 并保存
            mask_np = (mask.squeeze().cpu().numpy() * 255).astype(np.uint8)
            mask_img = Image.fromarray(mask_np)
            mask_img.save(os.path.join(SAVE_DIR, name))

            print(f'Successfully processed: {name}')

            # 每张图处理完清空显存，防止碎片堆积
            torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
