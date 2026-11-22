"""摩尔纹仿真器：用**栅格错配**产生真实走样干涉，而不是叠一层正弦条纹。

为什么不用"加正弦条纹"
----------------------
如果合成时手工画一组周期条纹，再拿"找周期条纹"的算子去检测，那是把答案写进题目
里 —— 指标必然好看，与真实摩尔纹无关。所以这里只模拟物理成因：

  显示像素栅格（pitch = p 像元/图素）  ⊗  相机传感栅格（1 像元，且相对旋转 θ、
  缩放误差 ε）→ 两栅格不可公度 → 采样必然走样 → 走样折叠到低频就是摩尔纹。

θ 与 ε 决定拍频：旋转产生的条纹周期 ≈ p/θ(rad)，缩放误差产生的 ≈ p/ε。
调这两个旋钮就能覆盖从粗大彩色带（几十像素）到细密纱状纹（几像素）的全谱。

后再叠上真实拍摄链的其余环节：光学模糊、传感器噪声、JPEG、渐晕。
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.ndimage import map_coordinates


def add_moire(img_u8: np.ndarray, pitch: float = 3.0, angle_deg: float = 1.2,
              scale_err: float = 0.0, blur: float = 0.8, noise: float = 3.0,
              strength: float = 1.0, rng: np.random.Generator | None = None) -> np.ndarray:
    """给一张数字图加上拍屏摩尔纹。strength=0 时退化为"只走拍摄链不加干涉"的对照。"""
    rng = rng or np.random.default_rng(0)
    h, w = img_u8.shape[:2]

    # 1) 显示栅格：把内容量化成 pitch 大小的像素块（屏幕像素是有面积的）
    dw, dh = max(1, int(round(w / pitch))), max(1, int(round(h / pitch)))
    grid = cv2.resize(img_u8, (dw, dh), interpolation=cv2.INTER_AREA)
    grid = cv2.resize(grid, (w, h), interpolation=cv2.INTER_NEAREST)

    # 2) 传感栅格：以旋转 θ + 缩放误差 ε 的网格去最近邻采样上面这块"屏幕"
    th = np.deg2rad(angle_deg) * strength
    s = 1.0 + scale_err * strength
    M = np.array([[s * np.cos(th), -s * np.sin(th),
                   w / 2 - (s * np.cos(th) * w / 2 - s * np.sin(th) * h / 2)],
                  [s * np.sin(th), s * np.cos(th),
                   h / 2 - (s * np.sin(th) * w / 2 + s * np.cos(th) * h / 2)]],
                 dtype=np.float64)
    warped = cv2.warpAffine(grid, M, (w, h), flags=cv2.INTER_NEAREST,
                            borderMode=cv2.BORDER_REPLICATE)

    # 3) 与"没走样的理想采样"混合：strength<1 时干涉被部分抑制，便于做剂量响应
    if strength < 1.0:
        ideal = cv2.warpAffine(grid, M, (w, h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
        warped = cv2.addWeighted(warped, strength, ideal, 1 - strength, 0)

    # 4) 光学模糊
    if blur > 0:
        warped = cv2.GaussianBlur(warped, (0, 0), blur)

    # 5) 渐晕（拍屏常见四角偏暗）+ 传感器噪声 + JPEG 压缩
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
    warped = warped * (1.0 - 0.10 * r2)[..., None]
    out = warped.astype(np.float32) + rng.normal(0, noise, warped.shape).astype(np.float32)
    ok, buf = cv2.imencode(".jpg", np.clip(out, 0, 255).astype(np.uint8),
                           [cv2.IMWRITE_JPEG_QUALITY, 88])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else np.clip(out, 0, 255).astype(np.uint8)


def homography_fields(size, angle0=0.7, scale0=0.004, dangle=0.0, dscale=0.0,
                      axis="y", cx=None, cy=None, dd=0.025):
    """造一个**俯仰+偏航+轻微景深**的观看几何，返回 (panel坐标X, Y) 与局部失配量。

    为什么用单应而不是逐像素自由场：任意 (θ(x,y), s(x,y)) 不保证是可积坐标变换，
    画出来会是撕裂的乱纹。刚性感官的运动学天然给出平滑且局部可积的参数场——
    这正是"多参数平滑协变"的物理来源：
      俯仰/偏航 → 屏到相机的**比例随位置变**（透视缩短）
      偏航      → 屏两族栅格在像平面上的**投影方向随位置变**
      景深      → 离焦模糊随位置变（幅度随位置变）
    dangle/dscale 是沿 axis 的线性漂移率（每归一化坐标单位），0 时退化为全图常数。
    """
    if cx is None:
        cx, cy = angle0, scale0
    gx = np.linspace(-0.5, 0.5, size, dtype=np.float64)
    GX, GY = np.meshgrid(gx, gx)
    t = (GY if axis == "y" else GX) * 2.0
    th = np.deg2rad(cx + dangle * t)
    s = 1.0 + cy + dscale * t
    k = dd * (GX if axis == "y" else GY)
    ct, st, sc = np.cos(th), np.sin(th), 1.0 - k
    # 传感器 → 面板：先缩放 k，再 R(θ)·diag(s,s)，最后平移到画面中心
    X = size / 2 + ((ct * s) * GX - (-st * s) * GY) * sc
    Y = size / 2 + ((st * s) * GX + (ct * s) * GY) * sc
    return X, Y, th, s - 1.0, k


def add_screen_moire_homog(img_u8, p=3.0, bleed=0.18, blur=0.7, noise=3.0,
                          rng=None, X=None, Y=None, th=None, ds=None,
                          return_fields=False, cfa=True, ksub=4):
    """用局部参数场（可协变）渲染拍屏。同时返回**解析真值**：局部拍频图。

    拍频不用估，是算出来的。面板两族栅格 g1=(1,0)、g2=(0,1) 经局部雅可比 J 映到
    像平面成 a=Jg，被整数传感器栅格采样后只能看见其**混叠** b=a-round(a)，于是
      周期 = 1/|b|（px），方向 = 条纹 ⊥ b。
    摩尔纹的全部规律就是这一行：两套周期结构相碰，差频折叠到低频。
    """
    rng = rng or np.random.default_rng(0)
    h, w = img_u8.shape[:2]
    imgf = img_u8.astype(np.float32)
    gx = np.arange(w, dtype=np.float64)
    gy = np.arange(h, dtype=np.float64)

    # 坐标网格 GX/GY 是归一化的（间距 1/n），所以 np.gradient 给出的每**下标**导数
    # 要乘回 n 才是每像素雅可比（实测：不乘则 dX/didx=0.0020，×512 后才是 ≈1.01）
    sn = float(max(h, w))
    sx = np.gradient(X, axis=1) * sn; sy = np.gradient(X, axis=0) * sn
    tx = np.gradient(Y, axis=1) * sn; ty = np.gradient(Y, axis=0) * sn

    # 1) 面板渲染：把每个传感器像元的**方形孔径**点采样 K×K 个 sub-cell，再平均。
    # 前三次失败的方法均实测过：
    #   a) INTER_LINEAR 重采样周期 3px 的锐利方波 → 被双线性核抹掉（纹 0.04 灰阶）
    #   b) 在面板坐标上做解析孔径积分 → 孔径整体落在亮区里，梳子恒亮（std=0.0007）
    #   c) 先建 sub-cell 锐利梳再 cv2.remap → OpenCV 不支持 4D 映射（直接报错）
    # 量纲（本轮实测纠正）：内容数组是"屏像素图"，1 格 = 1 屏像素 = 3 个子像素。
    # 混叠的门槛是**屏像素周期 P（sensor px）< 2**（奈奎斯特），不是 P<1：
    #   1/P = 0.63 → 折叠后 |b| = 0.37 → 周期 2.7px，正好是 0006/0210 的实测峰。
    #   即 P≈1.6 sensor px（屏比相机略粗）才是真实拍摄几何；旧结论"屏必须细于
    #   sensor"把 tri 当成 sensor 周期又拿 p 当解析周期，两边不一致才会怎么扫都扫不到。
    # 孔径宽度是**物理量**：1 个 sensor 像元 = dX/du ≈ 1.01 面板单位，与 tri 无关。
    # 旧代码把孔径铺成 0.75·tri，tri 一变孔径就跟着变，谱峰自然不随 pitch 走。
    K = int(ksub)
    tri = float(p)                                   # 屏像素周期（面板单位≈sensor px）
    ap = 1.0                                       # 传感器像元孔径（面板单位）
    kk = (np.arange(K, dtype=np.float64) - (K - 1) / 2.0) / K * ap
    off_x = np.broadcast_to(kk[None, :], (K, K))   # axis=1 → 列方向 sub-cell
    off_y = np.broadcast_to(kk[:, None], (K, K))   # axis=0 → 行方向 sub-cell
    Xs = X[:, :, None, None] + off_x[None, None, :, :]
    Ys = Y[:, :, None, None] + off_y[None, None, :, :]
    res = np.empty((h, w, 3), np.float32)
    for c in range(3):
        # 内容下标 = 面板坐标 / 屏像素周期 - 0.5（格心对齐）
        cx_arr = (Xs / tri - 0.5).ravel()
        cy_arr = (Ys / tri - 0.5).ravel()
        lum = map_coordinates(imgf[:, :, c], [cy_arr, cx_arr], order=1,
                              mode="nearest").reshape(h, w, K, K)
        # 三族子像素竖条：同一条周期 tri 的梳，各自错开 tri/3 = p（旧代码错写成 p/3）
        ph = np.remainder(Xs - c * tri / 3.0, tri) / tri
        stripe = np.where(ph < (1.0 - bleed) / 3.0, 1.0, bleed).astype(np.float32)
        phy = np.remainder(Ys, tri) / tri
        stripe = stripe * np.where(phy < 0.85, 1.0, 0.45).astype(np.float32)
        res[:, :, c] = (lum * stripe).mean(axis=(2, 3))

    # 2) Bayer CFA + 去马赛克（相位各通道不同 → 干涉带彩色）
    # cfa=False 只供诊断：把 CFA/去马赛克这一环从谱形影响里排除掉，量纲对照用
    if cfa:
        b = np.clip(res[:, :, 0], 0, 255).astype(np.uint8)
        g = np.clip(res[:, :, 1], 0, 255).astype(np.uint8)
        r = np.clip(res[:, :, 2], 0, 255).astype(np.uint8)
        cfa_img = np.zeros((h, w), np.uint8)
        cfa_img[0::2, 0::2] = b[0::2, 0::2]; cfa_img[1::2, 0::2] = g[1::2, 0::2]
        cfa_img[0::2, 1::2] = g[0::2, 1::2]; cfa_img[1::2, 1::2] = r[1::2, 1::2]
        res = cv2.cvtColor(cfa_img, cv2.COLOR_BayerBG2BGR).astype(np.float32)

    if blur > 0:
        res = cv2.GaussianBlur(res, (0, 0), blur)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
    res *= (1.0 - 0.10 * r2)[..., None]
    if rng is not None:
        res += rng.normal(0, noise, res.shape).astype(np.float32)
    ok, buf = cv2.imencode(".jpg", np.clip(res, 0, 255).astype(np.uint8),
                           [cv2.IMWRITE_JPEG_QUALITY, 88])
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else np.clip(res, 0, 255).astype(np.uint8)
    if not return_fields:
        return img

    # 3) 解析真值：两族栅格各自的混叠拍频 + 周期图与方向图
    # 梳在面板坐标里的周期就是 tri（=3p），必须与渲染用的同一个量，不能再拿 p 当周期
    pm = tri
    beats = []
    for gvec in ((np.ones_like(sx), np.zeros_like(sx)),
                 (np.zeros_like(sy), np.ones_like(sy))):
        a1 = (sx * gvec[0] + sy * gvec[1]) / pm     # a = J·g / pm  (cyc/px)
        a2 = (tx * gvec[0] + ty * gvec[1]) / pm
        b1, b2 = a1 - np.round(a1), a2 - np.round(a2)
        beats.append((b1, b2))
    mag = np.stack([np.hypot(b1, b2) for b1, b2 in beats])
    period = 1.0 / np.maximum(mag.min(axis=0), 1e-6)          # px，最大的那个周期
    sel = np.argmin(mag, axis=0)
    bb1 = np.choose(sel, [beats[0][0], beats[1][0]])
    bb2 = np.choose(sel, [beats[0][1], beats[1][1]])
    orient = np.degrees(np.arctan2(-bb1, bb2))                # 条纹方向 ⊥ b
    return img, dict(period=period, orient=orient, beats=beats, th=th, ds=ds)


def add_screen_moire(img_u8: np.ndarray, pitch: float = 1.60, angle_deg: float = 0.7,
                     scale_err: float = 0.004, bleed: float = 0.18, blur: float = 0.7,
                     noise: float = 3.0, rng: np.random.Generator | None = None,
                     dangle_deg: float = 0.0, dscale: float = 0.0, axis: str = "y",
                     return_fields: bool = False, cfa: bool = True, ksub: int = 4):
    """逐环节模拟“拍屏”：面板子像素发光 → 传感器栅格采样 → Bayer CFA → 去马赛克。

    与 add_moire 的区别：这里不直接“造条纹”，而是把两个真实存在的栅格（面板的
    RGB 子像素梳状结构、传感器的方形孔径）按不可公度的几何关系碰在一起，条纹作
    为走样的**后果**自己出现。相位错开是三通道各自的，所以拍出来的干涉带彩色。

    pitch = **一个屏像素在像平面上占多少 sensor 像元**（面板单位，与 dX/du 差约 1%）。
    混叠门槛是 pitch < 2（奈奎斯特）：pitch=1.60 → 1/1.60·1.01 = 0.63 → 折叠成
    |b|=0.37 → 拍频周期 2.7px，与 0006/0210 实测主峰（2.72~2.75px）同一量级。
    历史上这个参数被错当成“子像素周期”并隐含乘了 3，所以扫遍 0.15~3.0 都命中不了。

    dangle_deg/dscale 非零时，失配量沿 axis **平滑漂移**（多参数协变），此时载波
    不再是全图常数——这才是真实拍屏的样子，也是判据必须扛住的东西。
    """
    h, w = img_u8.shape[:2]
    n = max(h, w)
    X, Y, th, ds, _ = homography_fields(n, angle_deg, scale_err, dangle_deg, dscale, axis)
    return add_screen_moire_homog(img_u8, pitch, bleed, blur, noise, rng,
                                  X=X[:h, :w], Y=Y[:h, :w], th=th[:h, :w],
                                  ds=ds[:h, :w], return_fields=return_fields,
                                  cfa=cfa, ksub=ksub)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    src = sys.argv[1]
    im = cv2.imread(src)
    if im is None:
        raise SystemExit(f"读不到 {src}")
    im = im[:512, :512]
    tiles = [("original(digital)", im.copy())]
    for pitch in (2.0, 3.0, 4.0):
        for ang in (0.3, 0.7, 1.5):
            m = add_screen_moire(im, pitch=pitch, angle_deg=ang)
            cv2.putText(m, f"p={pitch} a={ang}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(m, f"p={pitch} a={ang}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (255, 255, 255), 2, cv2.LINE_AA)
            tiles.append((f"p{pitch} a{ang}", m))
    cols = 4
    blank = np.zeros_like(im)
    grid = np.vstack([np.hstack([tiles[i + j][1] if i + j < len(tiles) else blank
                                 for j in range(cols)])
                      for i in range(0, len(tiles), cols)])
    # .scratch 是本项目的过程件目录（已被 .gitignore 挡住），新克隆里不存在
    Path(".scratch").mkdir(exist_ok=True)
    cv2.imwrite(".scratch/synth_montage.png", grid)
    print(".scratch/synth_montage.png", grid.shape)
    print("参数：", [t[0] for t in tiles])
