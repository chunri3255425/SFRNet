import os
from torch.utils.data import Dataset
from PIL import Image
import random
import torchvision.transforms as transforms
import torchvision.transforms.functional as TF
import torch


class MoireTargetDataset(Dataset):
    def __init__(self, root_dir, is_train=True, crop_size=256, crop_type='center', transform=None):
        """
        Args:
            root_dir: 数据根目录
            is_train: 是否为训练模式 (用于控制数据增强)
            crop_size: 裁剪尺寸
            crop_type: 裁剪类型，可选 'random', 'center'
        """
        self.root_dir = root_dir
        self.moire_dir = os.path.join(root_dir, 'moire')
        self.target_dir = os.path.join(root_dir, 'target')

        # 将参数保存到 self，修复之前的 AttributeError
        self.is_train = is_train
        self.crop_size = crop_size
        self.crop_type = crop_type.lower()

        if transform is None:
            self.transform = transforms.Compose([
                transforms.ToTensor(),
            ])
        else:
            self.transform = transform

        self.image_filenames = sorted(
            [x for x in os.listdir(self.moire_dir) if self.is_image_file(x)]
        )

    def is_image_file(self, filename):
        return any(filename.lower().endswith(extension)
                   for extension in ['.png', '.jpg', '.jpeg', '.bmp'])

    def __len__(self):
        return len(self.image_filenames)

    def __getitem__(self, idx):
        img_name = self.image_filenames[idx]

        moire_path = os.path.join(self.moire_dir, img_name)
        target_path = os.path.join(self.target_dir, img_name)

        # 读取图片
        moire_img = Image.open(moire_path).convert('RGB')
        target_img = Image.open(target_path).convert('L')  # 单通道读取

        # --- 修改后的裁剪逻辑 ---
        # 仅当 is_train 为 True 时执行裁剪，否则跳过，保持原图大小
        if self.is_train:
            if self.crop_type == 'random':
                i, j, h, w = transforms.RandomCrop.get_params(
                    moire_img, output_size=(self.crop_size, self.crop_size)
                )
                moire_img = TF.crop(moire_img, i, j, h, w)
                target_img = TF.crop(target_img, i, j, h, w)

            elif self.crop_type == 'center':
                moire_img = TF.center_crop(moire_img, (self.crop_size, self.crop_size))
                target_img = TF.center_crop(target_img, (self.crop_size, self.crop_size))

        # --- 后处理与 Tensor 转换 ---
        moire_img = self.transform(moire_img)
        target_img = transforms.ToTensor()(target_img)

        # 【核心修正】严格二值化
        target_img = (target_img > 0.5).float()

        return moire_img, target_img