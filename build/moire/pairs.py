"""生成**图内配对**的摩尔纹真值数据：同一张图里既有有纹区也有无纹区，标签精确到像素。

为什么必须图内配对
------------------
"摩尔纹照片 vs 数字截图"这种图间对照，混淆变量是拍摄链本身（噪声/JPEG/色偏/失焦/内容
分布），测出来的分数与摩尔纹无关。图内配对把拍摄链消成常量：同一张图、同一条链、同一
内容，唯一差别是局部栅格是否错配。任何能分开的量都只能是空间结构量。

怎么保证不循环论证
------------------
标签来自**几何参数**（angle/scale_err 是否为零），不来自任何判据。所以用这套数据验证
判据，不存在"用判据造标签再用该判据验证"的回路。

混合场必须是平滑的：若按硬边界分块拼接，接缝本身会成为捷径线索（检测器去学"接缝"）。
这里用低频随机场 + 高斯羽化，强度渐变连续，与真实拍屏时"条纹随局部对比度起伏"一致。
"""

from __future__ import annotations

import cv2
import numpy as np

from synth import add_screen_moire


def smooth_field(h: int, w: int, blob: int, coverage: float,
                 rng: np.random.Generator) -> np.ndarray:
    """低频 0/1 随机场：blob 控制团块尺寸，coverage 控制有纹面积占比。"""
    gy, gx = max(2, h // blob), max(2, w // blob)
    low = rng.random((gy, gx)).astype(np.float32)
    a = cv2.resize(low, (w, h), interpolation=cv2.INTER_CUBIC)
    a = np.clip(a, 0, 1)
    a = cv2.GaussianBlur(a, (0, 0), blob / 3.0)
    thr = np.quantile(a, 1.0 - coverage)
    m = (a > thr).astype(np.float32)
    # 再羽化一次，确保 0→1 连续过渡（尺度 ≥ blob/2）
    return cv2.GaussianBlur(m, (0, 0), blob / 4.0).clip(0, 1)


def make_pair(content_u8: np.ndarray, size: int = 512, pitch: float = 2.0,
              angle_deg: float = 0.9, scale_err: float = 0.010, blob: int = 128,
              coverage: float = 0.45, seed: int = 0,
              dangle_deg: float = 0.0, dscale: float = 0.0, axis: str = "y",
              return_fields: bool = False):
    """返回 (合成图, 有纹掩码[0..1], 元信息)。掩码由几何参数决定，与任何判据无关。

    dangle_deg/dscale 非零时走**协变**分支：失配量沿 axis 平滑漂移，载波频率与
    方向随位置变。注意标签仍然安全——纹在有纹区**始终存在**，只是参数在漂，
    所以“有/无”由 a 场决定，不随参数变。这正是真实拍屏的情形。
    """
    rng = np.random.default_rng(seed)
    base = cv2.resize(content_u8, (size, size), interpolation=cv2.INTER_AREA)
    geo = dict(pitch=pitch, blur=0.7, noise=3.0,
               dangle_deg=dangle_deg, dscale=dscale, axis=axis)
    # 两遍都用同一几何中心与同一 pitch，只有错配量不同；噪声用不同种子（与标签无关）
    clean = add_screen_moire(base, angle_deg=0.0, scale_err=0.0, return_fields=return_fields,
                             rng=np.random.default_rng(seed + 1), **geo)
    moire = add_screen_moire(base, angle_deg=angle_deg, scale_err=scale_err,
                             return_fields=return_fields,
                             rng=np.random.default_rng(seed + 2), **geo)
    if return_fields:
        clean, cf = clean
        moire, mf = moire
    a = smooth_field(size, size, blob, coverage, rng)
    out = clean.astype(np.float32) * (1 - a)[..., None] + moire.astype(np.float32) * a[..., None]
    ok, buf = cv2.imencode(".jpg", np.clip(out, 0, 255).astype(np.uint8),
                           [cv2.IMWRITE_JPEG_QUALITY, 88])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    # 二值化标签：羽化边缘 ±blob/8 内的像素算"不确定"，用 nan 掩码剔除
    hard = (a > 0.6).astype(np.uint8)
    ambig = ((a > 0.4) & (a < 0.6)).astype(bool)
    meta = dict(ambig=ambig, angle=angle_deg, scale_err=scale_err,
                pitch=pitch, blob=blob, coverage=coverage,
                dangle=dangle_deg, dscale=dscale, axis=axis)
    if return_fields:
        meta["fields"] = (cf, mf)
    return (img, hard, meta) if not return_fields else (img, hard, a, meta)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    src = sorted(Path("测试图片/普通屏幕截图").glob("*.png"))[:6]
    tiles = []
    for i, p in enumerate(src):
        im = cv2.imread(str(p))
        # 覆盖从细密到粗大的拍频：角度/缩放误差决定条纹周期
        for ang, se in ((0.4, 0.004), (1.2, 0.012), (3.0, 0.025)):
            img, m, meta = make_pair(im, angle_deg=ang, scale_err=se, seed=i * 7 + int(ang))
            vis = np.hstack([img, cv2.cvtColor((m * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)])
            cv2.putText(vis, f"a={ang} e={se}", (8, 24), 0, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(vis, f"a={ang} e={se}", (8, 24), 0, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
            tiles.append(cv2.resize(vis, (512, 256)))
    grid = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
    # .scratch 是本项目的过程件目录（已被 .gitignore 挡住），新克隆里不存在
    Path(".scratch").mkdir(exist_ok=True)
    cv2.imwrite(".scratch/pairs_demo.png", grid)
    print(f".scratch/pairs_demo.png {grid.shape}  每行: 合成图 | 像素级真值掩码")
    if len(sys.argv) > 1:
        print("（可选）批量落盘目录:", sys.argv[1])
