# 板端上电取证与烧录手册（Purple Pi OH2 / RK3576）

> **状态**：首次上电实测已完成，出厂固件取证数据已全部落盘（本文是唯一的原始数据副本）
> **实测时间**：2026-11-06，开发机为 WSL2 Ubuntu，经 Windows 侧 `adb.exe` 驱动
> **板子**：触觉智能 Purple Pi OH2（RK3576，实购 SKU 4GB+64GB），序列号见 §1
> **配套**：硬件规格断言见 [SDD §1.1](../design/SOFTWARE_DESIGN.md)，厂商手册清单见 [vendor/README.md](../vendor/README.md)

本文只放**实测事实**与**操作序列**。凡 `[实测]` 都可照 §8 的命令重跑复核；凡未标 `[实测]` 的
都是厂商文档原文或本文作者的推断，已单独注明。

---

## 1. 出厂身份（⚠️ 刷机即失，本文是唯一留存）

| 项 | 值 | 来源 |
|---|---|---|
| 序列号 SN | **`98fed70982ab4c63`** | `[实测]` `getprop ro.serialno`，与 adb 设备号、USB InstanceId 三处一致 |
| eth0 MAC | `42:0c:f6:ef:5e:7b` | `[实测]` `/sys/class/net/eth0/address` |
| eth1 MAC | `46:0c:f6:ef:5e:7b` | `[实测]` 同上。⚠️ 与 eth0 **只差首字节**（42/46），即两个本地管理位派生自同一基址，写号时不能按"两个独立 OUI"处理 |
| wlan0 MAC | `10:a5:62:1a:11:00` | `[实测]` |
| 出厂系统 | Android 14 **userdebug** | `[实测]` `ro.bootimage.build.fingerprint` = `rockchip/rk3576_u/rk3576_u:14/UQ1A.240205.004.B1/tbr09191354:userdebug/release-keys` |
| 构建标识 | `eng.tbr.20250919.104826` | `[实测]` `ro.build.display.id` |
| 内核 | `Linux 6.1.141 #52 SMP PREEMPT Fri Sep 19 10:39:52 CST 2025 aarch64` | `[实测]` `uname -a` |
| 引导锁状态 | `androidboot.verifiedbootstate=orange` | `[实测]` `/proc/cmdline`。**orange = 未锁定**，刷第三方固件无需解锁 |
| 固件组件版本 | `ddr-v1.10-9071d8ccd4, spl-v1.08, bl31-v1.21, bl32-v1.07, uboot-09/19/2025` | `[实测]` `androidboot.fwver` |
| 默认 shell 权限 | `uid=2000(shell)`，SELinux `u:r:shell:s0`；`adb root` 后可提到 `uid=0` + `u:r:su:s0` | `[实测]` userdebug 才允许 `adb root`，正式量产固件未必 |

> 取证当时板子的 USB 状态：Windows 枚举 `USB\VID_2207&PID_0006\98FED70982AB4C63`（Status OK，
> 名 `rk3576_u`），ADB 接口 `USB\VID_18D1&PID_4E11` 为在位设备。**即"接上 Type-C 就在跑系统"，
> 不是 Loader/Maskrom 模式**——这一点值得记下来，因为它决定了取证窗口每次上电都能重开。

---

## 2. 取证结果对照表：文档原本挂着的问题 → 实测答案

| 文档挂着的项 | 实测结论 | 对设计的影响 |
|---|---|---|
| **SDD §1.1 CPU 基线**（更正前写"4×A72 @2.2GHz + 4×A53 @2.2GHz"） | ⚠️ **一半成立、一半证伪**。`policy0` = Cortex-A53×4（`CPU part 0xd03`）档位 408/600/816/1008/1200/1416/1608/1800/**2016** MHz；`policy4` = Cortex-A72×4（`0xd08`）同前 8 档 + **2208** MHz。A72 的 2.2 GHz 对得上，**A53 根到不了 2.2 GHz** | **A53 档顶 2016 MHz 是新增事实且推旧值**，软回退（`OnnxRuntime CPU` 跑在 A53）的预算应按 2.016 GHz 而非 2.2 GHz 估 |
| **SDD §1.1 内存类型口径冲突**（主图 LPDDR5 vs 规格页 LPDDR4/4X@4266） | ⚠️ **"LPDDR5"不成立，但具体类型仍不断言**。`dmc available_frequencies` = **534 / 1320 / 1968 / 2736 MHz** 四档，`normal_rate = 1968000000`；`MemTotal` = 3,990,676 kB（4 GB SKU 吻合）。档位表里**没有 LPDDR5 应有的 3200+ 档**，最高仅 2736 | 内存类型只影响吞吐预期，不改接口。要坐实类型需厂商 SDK 的 dts 或客服口径；板上已确认读不到：debugfs 未挂载、无 `/proc/ddrinfo`、无 `/proc/device-tree` 的 ddr 类型节点 |
| **6 TOPS 推算** | 新增依据：NPU devfreq 节点 `/sys/class/devfreq/27700000.npu`，档位 **300–950 MHz**（300/400/500/600/700/800/900/950），governor `rknpu_ondemand` | 6 TOPS 是 950 MHz 顶档口径的厂商标称；`ondemand` 意味着**冷启动首帧会跑在低档**，实测延迟必须先 warmup 再计时（与 `torch_runtime` 的 warmup 治理同一动机） |
| **eMMC 5.1** | ✅ 成立。`mmc0: new HS400 Enhanced strobe MMC card`；`androidboot.boot_devices=2a2d0000.ufs,2a330000.mmc` | 存储带宽按 HS400 口径估，4K 帧落盘/读模型的 IO 预算不变 |
| **`librknnrt.so` 版本定 RKNN-Toolkit2 对齐口径** | ❌ **被证伪，此路不通**。在 `/vendor /system /odm /oem /apex /data` 全盘搜 `*rknn*` **命中 0**；`/vendor/bin` 无 `rknn_server`；`getprop` 只有 `*rockchip` 的 HAL 服务（health/light/memtrack/power-aidl/thermal），没有任何 rknn/npu 项 | 出厂安卓**没带 NPU runtime**。Toolkit2 版本口径只能改从厂商 Linux SDK 取（`4.软件资料/系统SDK/`），或烧完官方 Linux 后读 rknpu 驱动版本。详见 §3 |
| **摄像头接口接线**（更正前 SDD §1.1 只写"MIPI CSI ×2"） | 厂商《Linux 使用手册》原文：板载 **3 路**摄像头接口 J19/J20/J25，**J19 与 J25 默认适配 IMX415，J20 默认适配 OV13855（画面偏绿色）** | 已购 IMX415 必须插 **J19 或 J25**。插 J20 不会报错，只会得到偏绿画面——这类"能跑但结果是错的"最危险，写进验收清单 |

---

## 3. 被推翻的一条既有结论（写给后人）

`docs/vendor/README.md` 与 `CHANGELOG`「文献归档与安卓路线处置」都写过：

> 出厂预装的安卓是**免费的设备取证机会**（`librknnrt.so` 版本定 Toolkit2 对齐、dmesg 定 LPDDR
> 口径、cpufreq 定 A72/A53 基线），刷机前先拿完。

本轮实测：**三项里两项成立（dmesg 拿到 DDR 档位表、cpufreq 拿到大小核频率上限），
第一项落空**——这块板的出厂 Android 14 构建里根本没有 `librknnrt.so`。

所以"安卓能白拿 Toolkit2 版本口径"这个假设不成立，别再依赖它。但**这条取证路线整体仍然值得做**，
因为它白送了另外四件文档里长期靠猜的事：CPU 大小核身份与频率上限、NPU 调频档位、eMMC 代际、
以及 SN/MAC。结论：**取证窗口每次上电都能重开（§1 末尾），刷机前照 §8 跑一遍即可，不必抢。**

---

## 4. 固件选型（已定，含拒绝项）

**选定**：`预编译固件/屏幕固件/Ubuntu/IDO_PurplePiOH2_V1B_Ubuntu22.04_HDMI4K_250907R.img.zip`

| 项 | 值 |
|---|---|
| zip | 1,886,793,196 字节 |
| 内含 `.img` | 6,163,532,419 字节（6.16 GB）+ `MD5.txt` |
| 厂商声明 MD5 | `8cdd8d825ea8976239a702b7586f3406` |
| 完整性 | `[实测]` `unzip -t` 全量 CRC 校验通过（57 s，No errors detected）。解压后仍需比对 MD5 |

三个维度的取舍与理由：

| 维度 | 候选 | 选 | 理由 |
|---|---|---|---|
| rootfs | Buildroot2024 / Debian12 / Ubuntu22.04 | **Ubuntu 22.04** | 厂商《Linux 使用手册》通篇 `apt` 操作；板上 Python 3.10 与 `rknn-toolkit-lite2` 的 `cp310` wheel 口径天然对齐。Buildroot 包只有 517 MB、busybox 无包管理，装 OpenCV/V4L2 工具要交叉编译 |
| 显示变体 | HDMI4K / MIPI1200x1920 | **HDMI4K** | 功能支持列表里两者都勾选，但本项目不接 MIPI DSI 屏；MIPI 变体要去初始化一块不存在的 1200x1920 面板 |
| 实时性 | 常规 / Preempt / Xenomai | **常规**（即"屏幕固件"那组） | 板上 4K 一帧 13–22 s，瓶颈是算力不是调度抖动；Xenomai 双内核与 Python/RKNN 用户态有摩擦。点板阶段要最标准的一份 |

**拒绝项留档**：
- `Buildroot2024` 那份（517 MB）留给**交付阶段**做无人值守精简根文件系统时再评估——它其实更贴合 SDD §1.2"上电即跑"的形态，但不适合现在迭代。
- `Xenomai` / `Preempt` 两份实时固件在本轮一律不碰。若将来实测发现帧间抖动是硬约束，再单独开一条验证线。
- 所有包都标 **V1B**。⚠️ 若手上板子是 V1A 或别的丝印版本，**先别烧**，回厂商要对应包。

---

## 5. 烧录操作序列（⚠️ 只能在 Windows 主机上做）

厂商《上手指南》§八「固件烧录」**通篇只有 USB 直连烧 eMMC，没有任何 SD 卡启动路径**；
《Linux 使用手册》里 TF 卡座的定位是外设存储（功能表原文写"TF卡(测试传输8G文件)"），
且警告"emmc 分区的变化可能导致 tf 卡的分区也会变化，导致无法自动挂载"。
`SDDiskTool_v1.78` 内部的 `sd_boot_config.config` 文件头自述是 "rockchip sdcard boot config file
**for factory**"，字段为 `pcba_test / fw_update / demo_copy`——它面向产线与批量升级卡，不是开发路径。

1. Windows 装驱动：`Tools/RK驱动/DriverAssitant_v5.13.zip` → `DriverInstall.exe` → 【驱动安装】
2. 把 zip **解压到 Windows 本地盘**再烧。不要让 RKDevTool 从 `\\wsl.localhost\...` 读 6.16 GB，9p 共享会慢到不可用
3. `RKDevTool.exe` → **断开板子电源** → Type-C 接板子 **OTG 口** → **按住 Recovery 键不放** → 上电，约 2 s 后应显示"发现一个 LOADER 设备"
4. 【升级固件】→【固件】选中 `.img` → 加载后**核对芯片信息为 RK3576** → 【升级】
5. 加载 6.16 GB 的包会有明显卡顿甚至短暂无响应，属正常，别强退
6. 烧完底部出现"**发现一个 ADB 设备**"= 成功
7. ⚠️ **不要点【高级】→【擦除所有】**：厂商指南原话"擦除烧录会清除 SN、MAC 地址等 RK 写号工具烧录的数据"。只有常规烧录失败才考虑，且必须先确认 §1 的 SN/MAC 已留存
8. Loader 进不去时的最后手段：短接主板 **Boot 测试点** 上电进 Maskrom（功能表"Boot键/boot测试点"✅）

**串口控制台**：`/proc/cmdline` 里 `console=ttyFIQ0`、`earlycon=uart8250,mmio32,0x2ad40000`。
⚠️ RK35xx 的 FIQ 调试串口是 **1500000-8N1，不是 115200**；用 CH340 + MobaXterm 接错波特率只会看到满屏乱码。

---

## 6. 开发机侧通道（WSL2 的事实与限制）

| 项 | 结论 |
|---|---|
| WSL 互操作 | **本来就是开的**。`/etc/wsl.conf` 里 `[interop] enabled=true`、`appendWindowsPath=false`；binfmt `WSLInterop` 为 `enabled`。所以裸命令 `cmd.exe` 找不到，但**绝对路径一直可用**：`/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`、`cmd.exe` 实测能跑 |
| 从 WSL 驱动 Windows adb | ✅ 可行，本文全部数据就是这么拿的。`unzip Tools/ADB工具/adb_win.zip` 后 **`chmod +x adb.exe`**（ext4 上没执行位会被拒），再 `./adb.exe shell "..."`。两个坑：Windows 侧输出是 **GBK**（管道接 `tr -d '\r'`，中文再走 `iconv -f gbk -t utf-8`）；参数里的 `$` 必须转义，否则被 bash 吃掉 |
| 从 WSL 烧录 | ❌ **不行**。互操作只能调用 Windows 程序，搬不动 USB 设备；本机无 `usbip`/`usbipd`、无 `rkdeveloptool`。要做需 Windows 侧装 usbipd-win + WSL 内核 usbip 客户端，比直接开 Windows 的 RKDevTool 麻烦得多，不建议 |
| 副作用提醒 | `adb root` 会把 adbd 提到 root（`u:r:su:s0`）并重启 adbd。收尾可 `./adb.exe unroot && ./adb.exe kill-server` |

---

## 7. 仓库侧处置（本轮一并做的）

- `.gitignore` 新增 `Tools/`（4.8 GB，Windows 侧烧录/调试工具链 + VirtualBox + Ubuntu 22.04 ISO）与
  `预编译固件/`（14 个 update.img 压缩包，19 GiB）。两者原本处于"未跟踪但也没被忽略"状态，
  一次 `git add -A` 就会把二进制永久写进历史。已用 `git check-ignore -v` 验证命中。
- `Tools/` 内 8 个 `(1)` 副本经 md5 逐条比对与正本完全相同，已删除（82 MB）。
- `虚拟机/Vbox/ubuntu-22.04.5-desktop-amd64.iso.baiduyun.p.downloading` 经 ISO9660 主卷描述符核对
  **内容完整**（声明 2,325,541 扇区 × 2048 = 4,762,707,968 字节 = 实际大小），已去掉后缀重命名。
  它是 x86_64 PC 镜像，只用于 RKNN-Toolkit2 转换环境，**不是板端镜像，不能写进 SD 卡**。

---

## 8. 复采命令清单（板子正常上电 + Type-C 接 Windows 后，在 WSL 里逐条跑）

```bash
ADB=/home/plus/learn/SFRNet/Tools/ADB工具/adb_win/adb.exe
$ADB devices -l                      # 应见 98fed70982ab4c63 device product:rk3576_u
$ADB root                            # userdebug 才允许；adbd 会重启，等 2~3 s

# 身份与固件
$ADB shell "getprop ro.serialno; getprop ro.bootimage.build.fingerprint; uname -a"
$ADB shell "cat /proc/cmdline"       # verifiedbootstate / fwver / boot_devices / console

# CPU 大小核与频率上限
$ADB shell "grep -E 'processor|CPU part' /proc/cpuinfo"
$ADB shell "for p in /sys/devices/system/cpu/cpufreq/policy*; do echo \$p \$(cat \$p/scaling_available_frequencies); done"

# 内存 / DDR 档位 / eMMC 代际
$ADB shell "head -3 /proc/meminfo"
$ADB shell "cat /sys/class/devfreq/dmc/available_frequencies"
$ADB shell "dmesg | grep -i -E 'dmc|mmc|fwver'"

# NPU 调频档位（注意 governor 是 ondemand，测延迟前必须 warmup）
$ADB shell "cat /sys/class/devfreq/27700000.npu/available_frequencies /sys/class/devfreq/27700000.npu/governor"

# NPU runtime 复查（每轮换固件都该重跑一次，确认是否仍无 librknnrt）
$ADB shell "find /vendor /system /odm /oem /apex /data -name '*rknn*' 2>/dev/null | head"

# MAC（刷机即失）
$ADB shell "for n in /sys/class/net/*; do echo \$(basename \$n) \$(cat \$n/address); done"
```

---

## 9. 还欠着的项

| 项 | 状态 | 下一步 |
|---|---|---|
| DDR 具体类型（LPDDR4 还是 4X） | 未坐实 | 板上读不到（debugfs 未挂、无 ddr 节点）；只能从厂商 SDK dts 或客服口径拿 |
| RKNN-Toolkit2 版本对齐 | 未定 | 下 `4.软件资料/系统SDK/`，或烧完官方 Linux 后读 rknpu 驱动版本 |
| 解压后 `.img` 的 MD5 比对 | 未做 | 解压后 `md5sum` 对 `8cdd8d825ea8976239a702b7586f3406` |
| 烧完 Linux 后的首帧实测 | 未做 | 复核"120–200 ms/块、4K 一帧 13–22 s"，注意先 warmup（§2 NPU 行） |
