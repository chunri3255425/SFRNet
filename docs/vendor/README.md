# 厂商资料（PDF 不入库）

本目录收纳 **触觉智能（Industio）** 的板卡手册与规格书。PDF 合计约 26 MB，属可重新下载的
厂商副本，已在 `.gitignore` 中以「`目录/*` + `!.gitkeep`」的方式排除——**目录结构入库，内容不入库**。
clone 后目录是空的，需要时自行下载放回原文件名即可。

## 该去哪下

官网 `http://www.industio.cn`，进对应产品的「资料下载」页取最新版；网盘资料可联系客服索取。
本仓库内的副本是采购时随附的那一批，命名保持厂商原样，便于和官网清单对得上。

## purplepi-oh2/ —— 整板（RK3576，实购 SKU 4GB+64GB）

| 文件 | 用途 | 什么时候要翻 |
|---|---|---|
| `IDO-Purple_Pi_OH2开发板上手指南.pdf` | 刷机、接口、启动介质 | 第一阶段点板子之前 |
| `IDO-Purple_Pi_OH2_Linux使用手册.pdf` | Linux 侧外设与系统操作 | 跑 `--serve-net` / 取帧联调 |
| `IDO-Purple_Pi_OH2_Linux开发手册.pdf` | SDK、内核、驱动、RKNN 相关 | 模型转换与 rknn 后端联调 |
| `IDO-Purple_Pi_OH2_OpenHarmony使用手册.pdf` | OH 侧系统操作 | 双系统对比时才用 |
| `IDO-Purple_Pi_OH2_开发板规格书.pdf` | 载板接口、供电、尺寸 | 接线与结构件设计 |

## som7609/ —— 核心板（模组）

| 文件 | 用途 |
|---|---|
| `IDO-SOM7609-S1_核心板规格书.pdf` | S1 模组引脚、电气、功耗 |
| `IDO-SOM7609-S1_硬件设计指南.pdf` | 载板设计约束（本项目不改载板，备查） |
| `IDO-SOM7609-S2_核心板规格书.pdf` | S2 模组规格；**与 S1 有差异，改板或换料前先核对是哪一版** |

## 为什么没有安卓手册

设计文档从头到尾假设 Linux：打包脚本落 `/opt/moireguard/` + systemd 单元，
`app/adaptation/runtime/rknn_runtime.py` 依赖 Python 的 `rknn-toolkit-lite2`，取帧走 V4L2
（`/dev/video*`），`device_io/` 只有 `linux_io.py`。换到安卓这三条都要重写成
init 服务 + NDK `librknnrt.so` + Camera HAL，不是"暂不支持"而是整条依赖链不成立。
详见 [CHANGELOG 的「文献归档与安卓路线处置」](../archive/CHANGELOG.md)。

> 例外：板子出厂若预装安卓，它是**免费的设备取证机会**——`librknnrt.so` 的版本能定
> Toolkit2 的对齐口径，`dmesg` 能定 LPDDR 实际规格，`cpufreq` 能定 A72/A53 基线。
> 刷机覆盖之前先把这几项读完，这跟"是否采用安卓路线"无关。
