"""像素级摩尔纹标注：用从图像自身频谱量出的载波，逐像素判定"纹是否可见"。

判据（先固定，再产出标签；标签不来自任何待验证的检测器）
----------------------------------------------------------
载波频率不是猜的，是从取样块 2D 频谱的峰位读出来的：本例天空在周期 2.6~2.8px、
方位≈90° 处有强窄带峰，且分裂为两支（0.3516 / 0.3906 cyc/px），两支之差对应
拍频周期≈27px —— 即屏幕栅格基频 + 摩尔边带。

逐像素分数定义为**该窄带载波占局部反差的比例**：

    r = 平滑|解析带通响应| / (平滑局部标准差 + eps)

分子只在载波方向有规则周期结构时才大；分母是该处的总反差。于是：
  - 平滑内容 + 有纹（天空）→ r→1
  - 高频内容遮蔽（灌木）→ 分母被内容抬高，r→小
  - 无发光（黑边）→ 分子是噪声，r→小
r 无量纲，不受亮度/对比度/色偏影响，这正是图间对照里死活分不开的原因所在。

输出：原色底 + 仅在有纹像素处叠红（其余像素一个字节都不改）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


def analytic_bandpass(gray: np.ndarray, f0: tuple[float, float], sigma: float) -> np.ndarray:
    """单边（解析）二维高斯带通 → 复包络。只取 +f0 一支，避免驻波歧义。"""
    h, w = gray.shape
    fx = np.fft.fftfreq(w).astype(np.float64)
    fy = np.fft.fftfreq(h).astype(np.float64)
    FX, FY = np.meshgrid(fx, fy)
    G = np.exp(-((FX - f0[0]) ** 2 + (FY - f0[1]) ** 2) / (2.0 * sigma ** 2))
    F = np.fft.fft2(gray.astype(np.float64) - gray.mean())
    return np.fft.ifft2(F * G)


def box(a: np.ndarray, k: int) -> np.ndarray:
    return cv2.boxFilter(a.astype(np.float32), -1, (k, k), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


def estimate_carriers(gray: np.ndarray, tile: int = 512, fmin: float = 0.20,
                      fmax: float = 0.46, topk: int = 3, snr_min: float = 3.0,
                      bin_px: int = 8) -> list[tuple[float, float, int, float]]:
    """不靠先验、不靠猜：逐块取 2D 频谱主峰，投票得到全图主导载波。

    载波周期取决于屏幕栅距、观看距离、变焦、拍摄角度，不行得逐图估计。

    两个必须的先验（都是实测教训）：
    1) fmin=0.20（周期 ≤5px）。搜到 0.06 起会把**周期性内容**当载波：moire_0210 里
       木板墙的板缝周期 15.6/16.4px，能量票数（10/6）高于真网格峰 2.70px（票 4）。
       拍频包络（0.01~0.08）属于另一个独立假设，不在这里找。
    2) 排序按平均 SNR，不按票数×SNR。网格是发光面板的固有周期，与内容无关且 Q 值高；
       内容周期性只在部分图块出现，票数多但峰不尖。
    """
    h, w = gray.shape
    g = gray.astype(np.float32)
    win = np.hanning(tile).astype(np.float32)
    grid = np.arange(tile) - tile // 2
    GX, GY = np.meshgrid(grid, grid)
    rad = np.hypot(GX, GY) / tile
    band = (rad >= fmin) & (rad <= fmax)
    votes: dict[tuple[int, int], list] = {}
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            p = g[y:y + tile, x:x + tile]
            if float(p.std()) < 1.0:
                continue
            p = p - p.mean()
            F = np.abs(np.fft.fftshift(np.fft.fft2(p * win[:, None] * win[None, :]))) / tile ** 2
            L = np.log(F + 1e-8)
            Lb = np.where(band, L, -np.inf)
            iy, ix = np.unravel_index(np.argmax(Lb), Lb.shape)
            snr = float(Lb[iy, ix] - np.median(L[band]))
            if not np.isfinite(snr) or snr < snr_min:
                continue
            ux, uy = int(GX[iy, ix]), int(GY[iy, ix])
            if uy < 0 or (uy == 0 and ux < 0):     # 共轭归一
                ux, uy = -ux, -uy
            votes.setdefault((round(ux / bin_px) * bin_px,
                              round(uy / bin_px) * bin_px), []).append((snr, ux / tile, uy / tile))

    def score(vs):
        # 主键=平均 SNR（峰的尖锐度），票数只作同分时的次键
        return (float(np.mean([v[0] for v in vs])), len(vs))

    out = []
    for key, vs in sorted(votes.items(), key=lambda kv: score(kv[1]), reverse=True)[:topk]:
        out.append((float(np.mean([v[1] for v in vs])), float(np.mean([v[2] for v in vs])),
                    len(vs), float(np.mean([v[0] for v in vs]))))
    return out


def high_pass(gray: np.ndarray, fc: float = 0.12) -> np.ndarray:
    """去掉低频（含拍频包络）只剩细节层。

    为什么分母必须先高通：拍频周期与局部反差统计窗同量级（本图 ~27px vs 窗 48px），
    直接用原图局部标准差做分母，等于把摩尔纹自己的亮度起伏计进了参照，纹最强的
    地方分母反而最大 → 标签在天空里裂成条带空洞（自抵消）。先高通掉低频，分母就只
    反映内容细节（叶片、笔画、边缘、颗粒），与拍频无关。
    """
    h, w = gray.shape
    FX, FY = np.meshgrid(np.fft.fftfreq(w).astype(np.float64),
                         np.fft.fftfreq(h).astype(np.float64))
    f2 = FX ** 2 + FY ** 2
    H = 1.0 - np.exp(-f2 / (2.0 * fc ** 2))
    F = np.fft.fft2(gray.astype(np.float64) - gray.mean())
    return np.real(np.fft.ifft2(F * H)).astype(np.float32)


def moire_ratio(gray: np.ndarray, f0s: list[tuple[float, float]], sigma: float = 0.035,
                win: int = 48, fc: float = 0.12, metric: str = "frac",
                den_floor: float = 2.0, amp_floor: float = 0.8) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (占比图 r, 分子=载波包络, 分母=细节层局部反差)。

    r = 平滑|解析带通响应| / (平滑细节层标准差 + eps)

    真实网纹常是两族斜交条纹（本图频谱里就有 (0.0215,-0.3516) 与 (0.0215,+0.3906)
    两个独立方向），所以分子取各载波包络的最大值，而不是把响应相加。
    """
    hp = high_pass(gray, fc)
    # boxFilter 的 float32 累加会给出量级 ~1e-13 的负值，不 clip 会让 sqrt 产出 NaN 并污染全图
    sd = np.sqrt(np.clip(box(hp * hp, win), 0.0, None))
    num = None
    for f0 in f0s:
        env = box(np.abs(analytic_bandpass(gray, f0, sigma)).astype(np.float32), win)
        num = env if num is None else np.maximum(num, env)
    # 除以 √2 的倒数做归一：单一平面波时 num=A/2、细节层反差=A/√2，比值上限 0.707。
    # 地板两项缺一不可，否则平坦/纯黑区会爆：那里 num 和 sd 同时→仅余噪声，
    # num/(sd+1e-3) 能到几百（实测 p99=128）。sd 地板=能看见的最小内容反差（灰阶），
    # num 地板=网格幅度低于此就肉眼不可见（JPEG 噪声本身就有 1~2 灰阶）。
    if metric == "frac":
        # 乘回 √2 后 r 直接可读为“细节能量里有多大比例集中在该载波上”。
        # 缺点：内容本身多纹理时，纹即使肉眼可见也只占细节一小部分 → r 很小时会漏标。
        r = np.sqrt(2.0) * num / np.maximum(sd, den_floor)
    else:
        # metric="rel"：把纹自己的贡献从分母里剔掉。细节方差 = 内容 + 纹，
        # 纹贡献 A²/2 = 2·num²，故内容方差 = sd² - 2·num²。r = 纹幅/内容反差。
        content = np.sqrt(np.clip(sd * sd - 2.0 * num * num, 0.0, None))
        r = num / np.maximum(content, den_floor)
    r = np.where(num < amp_floor, 0.0, r)
    return r, num, sd


def _tile_peak(inner: np.ndarray, fmin: float, fmax: float, snr_min: float, ray_min: float = 0.69):
    """在一个图块内找环带主峰，返回 (fx, fy, snr, ray_snr) 或 None。

    两道门缺一不可：
    - snr：峰 vs 全环带中位数（排除“整个环带都平”的无结构块）
    - ray_snr：峰 vs **同一条射线上**其他半径的中位数。这是周期与锐边的唯一分界：
      网格在射线上只有一个亮点；水平板缝/文字笔画这类锐边沿整条射线都亮，
      在 0.37cyc/px 处同样能赢过环带中位数（实测：moire_0210 的红全贴在木板缝上）。
    L 是 log 幅度，差 0.69 即幅度 2 倍。
    """
    t = inner.shape[0]
    p = inner - inner.mean()
    win = np.hanning(t).astype(np.float32)
    F = np.abs(np.fft.fftshift(np.fft.fft2(p * win[:, None] * win[None, :]))) / t ** 2
    L = np.log(F + 1e-8)
    grid = np.arange(t) - t // 2
    GX, GY = np.meshgrid(grid, grid)
    rad = np.hypot(GX, GY) / t
    band = (rad >= fmin) & (rad <= fmax)
    if not band.any():
        return None
    Lb = np.where(band, L, -np.inf)
    iy, ix = np.unravel_index(np.argmax(Lb), Lb.shape)
    snr = float(Lb[iy, ix] - np.median(L[band]))
    if not np.isfinite(snr) or snr < snr_min:
        return None
    # GX 沿列（x）变化、GY 沿行（y）变化（meshgrid 默认 xy），而 L[iy, ix] 的 iy 是行、
    # ix 是列 → 必须 ux=GX[iy,ix]、uy=GY[iy,ix]。写成 uy,ux=GX,GY 会把载波转置成
    # (真fy, 真fx)：单边高斯中心偏离 |fx-fy|（近轴纹可达 0.3 cyc/px ≈ 6.8σ），包络
    # 响应退到噪声底，num 与真实幅度完全脱钩（合成自检：A=0.5~20 灰阶，num 恒为 0.080）。
    ux, uy = float(GX[iy, ix]), float(GY[iy, ix])
    if uy < 0 or (uy == 0 and ux < 0):     # 共轭归一到 +fy 支，与 estimate_carriers 同口径
        ux, uy = -ux, -uy
    # 沿同一方向取多个半径。uy/ux 是相对中心的偏移，必须加回中心才能索引到 fftshift 后的 L；
    # 直接拿偏移当绝对索引会采到图角（高频角落），ray_snr 恒为负 → 所有图块被误拒。
    c = t // 2
    rs = []
    for s in np.linspace(0.3, 2.6, 24):
        by, bx = int(round(c + uy * s)), int(round(c + ux * s))
        if not (0 <= by < t and 0 <= bx < t):
            continue
        if abs(by - iy) < 8 and abs(bx - ix) < 8:
            continue
        rs.append(L[by, bx])
    ray_snr = float(L[iy, ix] - np.median(rs)) if len(rs) >= 6 else -9.9
    if ray_snr < ray_min:
        return None
    return ux / t, uy / t, snr, ray_snr


def moire_ratio_tiled(gray: np.ndarray, tile: int = 256, stride: int = 128,
                      fmin: float = 0.20, fmax: float = 0.46, sigma: float = 0.045,
                      snr_min: float = 3.0, ray_min: float = 2.5, flat_min: float = 0.25,
                      fc: float = 0.12, den_floor: float = 2.0,
                      amp_floor: float = 0.8, metric: str = "frac",
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """逐窗局部版：每个窗口自己估载波、自己带通，Hann 加权拼回。

    为什么不用“径向窄度”或“相位相干”做门（两者均实测失败）：
    - 径向对比：射线上小半径处是内容的 1/f 本底，中位数被低频抬高，真网格也被拒
      （moire_0210 降到 0.2%）。
    - 相位相干：一条锐边经窄带滤波后会振出 ~f/σ ≈ 10 个周期的 ringing，线性相干度
      照样接近 1，分不开。
    真正的区别在**包络的空间分布**：光栅的包络铺满整窗，锐边的包络集中在一条线附近
    然后衰减。所以门量窗内平坦度 mean(|env|)/p90(|env|)，光栅→接近 1，单条边→很小。
    """
    h, w = gray.shape
    # 局部化是必须的：相机对屏视角在画面里连续变化，网格频率/方向也跟着漂移，
    # 一次全图 FFT + 窄带高斯（约 9.5% 带宽）跟不上漂移，响应会到处塌掉
    # （实测：moire_0210 全图法 num 中位数仅 0.05 灰阶，与肉眼可见的满屏网格矛盾）。
    if tile < stride * 2:
        raise ValueError("tile 至少应为 stride 的 2 倍，否则图块间无重叠")
    tile = min(tile, h, w)
    pad = tile // 4
    n = tile + 2 * pad
    gp = np.pad(gray.astype(np.float32), pad, mode="reflect")
    W = np.hanning(n).astype(np.float32)
    WT = W[:, None] * W[None, :]
    FX, FY = np.meshgrid(np.fft.fftfreq(n), np.fft.fftfreq(n))
    FX = FX.astype(np.float64); FY = FY.astype(np.float64)
    f2 = FX ** 2 + FY ** 2
    Hhp = (1.0 - np.exp(-f2 / (2.0 * fc ** 2))).astype(np.float32)
    num = np.zeros((h, w), np.float32)
    hp2 = np.zeros((h, w), np.float32)
    wsum = np.zeros((h, w), np.float32)
    off = (tile - stride) // 2          # 只写窗口中心的 stride×stride，四周交给 Hann 淡出
    ny = max(1, int(np.ceil((h - tile) / stride)) + 1)
    nx = max(1, int(np.ceil((w - tile) / stride)) + 1)
    n_ok = n_tot = n_flat = 0
    for iy in range(ny):
        wy = min(iy * stride, h - tile)
        for ix in range(nx):
            wx = min(ix * stride, w - tile)
            sub = gp[wy:wy + n, wx:wx + n]
            n_tot += 1
            inner = sub[pad:pad + tile, pad:pad + tile]
            pk = _tile_peak(inner, fmin, fmax, snr_min, ray_min)
            if pk is None:
                continue
            n_ok += 1
            fx0, fy0, _, _ = pk
            # FX/FY 是归一化频率(cyc/px)，fx0 也是 cyc/px，直接相减；
            # 不要乘 n 转成 bin 序号，否则在频率轴上偏离几百个 bin，响应恒为 0
            G = np.exp(-((FX - fx0) ** 2 + (FY - fy0) ** 2) / (2.0 * sigma ** 2))
            S = np.fft.fft2(sub.astype(np.float64))
            env = np.abs(np.fft.ifft2(S * G.astype(np.complex128)))
            hp = np.real(np.fft.ifft2(S * Hhp.astype(np.complex128)))
            a0, a1 = pad + off, pad + off + stride
            # 包络平坦度门：光栅铺满窗→高，单条锐边→低
            ec = env[pad:pad + tile, pad:pad + tile]
            flat = float(ec.mean() / (np.percentile(ec, 90) + 1e-6))
            if flat < flat_min:
                n_flat += 1
                continue
            wt = WT[a0:a1, a0:a1]
            sl = slice(wy + off, wy + off + stride), slice(wx + off, wx + off + stride)
            num[sl] += (env[a0:a1, a0:a1] * wt).astype(np.float32)
            # 与全图版口径一致：num 取包络均值，分母取细节层均方根（累平方后开方）
            hp2[sl] += (hp[a0:a1, a0:a1] ** 2 * wt).astype(np.float32)
            wsum[sl] += wt
    wsum = np.maximum(wsum, 1e-6)
    num /= wsum
    sd = np.sqrt(hp2 / wsum)
    if metric == "frac":
        r = np.sqrt(2.0) * num / np.maximum(sd, den_floor)
    else:
        content = np.sqrt(np.clip(sd * sd - 2.0 * num * num, 0.0, None))
        r = num / np.maximum(content, den_floor)
    r = np.where(num < amp_floor, 0.0, r)
    print(f"    图块: 总 {n_tot}｜估到窄带峰 {n_ok}｜峰有但包络不平坦被拒 {n_flat}｜通过两道门 {n_ok - n_flat}")
    return r, num, sd


def refine(mask: np.ndarray, min_area: int = 400) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = cv2.morphologyEx((mask.astype(np.uint8)) * 255, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[lab == i] = 255
    return keep


def overlay_red(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """只有被判定有纹的像素变红，其余保持原色（不做全图半透明染色）。"""
    out = bgr.copy()
    m = mask > 0
    out[m] = (0.35 * out[m] + 0.65 * np.array([0, 0, 255], np.float32)).astype(np.uint8)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("img")
    ap.add_argument("--fx", type=str, default="0.0", help="载波 x 分量 cyc/px，逗号分隔可给多个方向")
    ap.add_argument("--fy", type=str, default="", help="载波 y 分量 cyc/px，逗号分隔")
    ap.add_argument("--sigma", type=float, default=0.035)
    ap.add_argument("--win", type=int, default=48)
    ap.add_argument("--fc", type=float, default=0.12, help="细节层高通过渡频率 cyc/px")
    ap.add_argument("--thr", type=float, default=0.5, help="归一化占比图二值化门限")
    ap.add_argument("--den-floor", type=float, default=2.0, help="分母地板（灰阶）")
    ap.add_argument("--amp-floor", type=float, default=0.8, help="载波幅度地板（灰阶，num=A/2）")
    ap.add_argument("--metric", default="frac", choices=["frac", "rel"])
    ap.add_argument("--auto", action="store_true", help="全图频谱估载波（忽略 --fx/--fy）")
    ap.add_argument("--tiled", action="store_true", help="逐窗局部估载波（频率局部漂移时必须）")
    ap.add_argument("--ray-min", type=float, default=2.5,
                    help="径向窄度门：网格沿射线只一个亮点→高，文字行/锐边沿射线连亮→低。"
                         "早先记为“已否证”是错的：当时 _tile_peak 把载波转置，射线方向跟着镜像。"
                         "修好后实测：moire_0006/0210 在 2.5 处面积不变（15.2%%/7.3%%），"
                         "clean_0031 从 4.2%% 降到 0.0%%；图内窗级 AUC(strong vs none)=1.000")
    ap.add_argument("--flat-min", type=float, default=0.25,
                    help="包络平坦度门：光栅铺满窗→高，单条锐边→低。21 张批测：0.45→0.25 使"
                         "摩尔纹侧面积从 0.3~22.6%% 升到 3.3~26.1%%（moire_0013 1.4%%→7.7%%），"
                         "截图侧仅多出 clean_0046 一处 0.5%%；图内 thr=0.5 召回 74.7%%→84.4%%")
    ap.add_argument("--tile", type=int, default=256)
    ap.add_argument("--stride", type=int, default=128)
    ap.add_argument("--rect", default="", help="只在该矩形内判定 x0,y0,x1,y1（如显示器发光区）")
    ap.add_argument("--out", default=".scratch")
    a = ap.parse_args()

    bgr = cv2.imread(a.img)
    if bgr is None:
        print("读不到图:", a.img)
        return 1
    fxs = [float(v) for v in a.fx.split(",") if v.strip() != ""]
    fys = [float(v) for v in a.fy.split(",") if v.strip() != ""]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if a.tiled:
        r, num, sd = moire_ratio_tiled(gray, a.tile, a.stride, sigma=a.sigma, fc=a.fc,
                                       metric=a.metric, den_floor=a.den_floor,
                                       amp_floor=a.amp_floor, ray_min=a.ray_min,
                                       flat_min=a.flat_min)
    else:
        if a.auto:
            est = estimate_carriers(gray)
            print("自动估载波 (fx, fy, 票数, SNR_db):")
            for fx, fy, n, snr in est:
                per = 1.0 / max(np.hypot(fx, fy), 1e-6)
                ang = np.degrees(np.arctan2(fy, fx))
                print(f"  f=({fx:+.4f},{fy:+.4f}) 周期={per:6.2f}px 方位={ang:+6.1f}° 票={n} SNR={snr:.1f}")
            if not est:
                print("  未估到任何窄带峰 → 该图可能根本没解析出屏幕栅格"); return 3
            f0s = [(fx, fy) for fx, fy, _, _ in est]
        else:
            if not fxs or not fys:
                print("需给 --fx/--fy，或用 --auto/--tiled"); return 2
            if len(fxs) == 1 and len(fys) > 1:
                fxs = fxs * len(fys)
            if len(fxs) != len(fys):
                print("--fx/--fy 个数不匹配"); return 2
            f0s = list(zip(fxs, fys))
        r, num, sd = moire_ratio(gray, f0s, a.sigma, a.win, a.fc, a.metric,
                                 a.den_floor, a.amp_floor)
    hit = r > a.thr
    if a.rect:
        x0, y0, x1, y1 = [int(v) for v in a.rect.split(",")]
        zone = np.zeros_like(hit); zone[y0:y1, x0:x1] = True
        hit = hit & zone
    mask = refine(hit.astype(np.uint8) * 255)

    stem = Path(a.img).stem
    o = Path(a.out); o.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(o / f"{stem}_mask.png"), mask)
    cv2.imwrite(str(o / f"{stem}_red.png"), overlay_red(bgr, mask))
    # 占比图可视化（供我核对门限是否落在分布间隙上）
    rv = np.clip((r - r.min()) / (np.percentile(r, 99) - r.min() + 1e-6), 0, 1)
    cv2.imwrite(str(o / f"{stem}_ratio.png"), (rv * 255).astype(np.uint8))

    q = [1, 5, 25, 50, 75, 95, 99]
    mode = "tiled" if a.tiled else ("auto" if a.auto else "fixed")
    print(f"{stem} [{mode}]: σ={a.sigma} fc={a.fc} thr={a.thr} metric={a.metric}"
          f" 地板 den={a.den_floor} amp={a.amp_floor}")
    print("  占比图 r 分位:", {p: round(float(np.percentile(r, p)), 3) for p in q})
    print("  num(=A/2,灰阶) 分位:", {p: round(float(np.percentile(num, p)), 2) for p in (25, 50, 75, 95)})
    print(f"  判为有纹面积: {np.mean(mask > 0):.1%}")
    print(f"  → {o}/{stem}_red.png  {o}/{stem}_mask.png  {o}/{stem}_ratio.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
