# 板端上电取证与烧录手册（Purple Pi OH2 / RK3576）

> **状态**：首次上电取证完成；烧录前复核（镜像完整性 + 板级兼容性 + 落盘到 Windows 本地盘）完成，**只差最后点【升级】**（本文是唯一的原始数据副本）
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
| 板级标识（设备树） | model = `Industio EVB7609 V1 Board`；compatible = `rockchip,Industio-EVB7609V1-VS1.0.0-250716` + `rockchip,rk3576` | `[实测]` `/proc/device-tree/{model,compatible}`。⚠️ 这里的 **"V1" 是 EVB 底板代号，不是厂商区分的 OH2 PCBA 版本（V1A/V1B）**，别用它判版本 |
| eMMC 容量 | 120,832,000 × 512 B = **61,865,984,000 B = 57.6 GiB**（标称 64 GB） | `[实测]` `/sys/block/mmcblk0/size`。SDD §1.1 的 "4+64G" 此前只有订单为证，现已有板端实证 |
| 出厂 Android 分区 | `security/uboot/trust/dtbo/vbmeta/boot/misc/recovery/backup/cache/metadata/frp/baseparameter/super/userdata` → `mmcblk0p1..p15` | `[实测]` `/dev/block/by-name/`。Android 用 `super` 动态分区；刷 Ubuntu 后这套名字全部消失（新布局见 §4） |

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
| **`librknnrt.so` 版本定 RKNN-Toolkit2 对齐口径** | ❌ **被证伪，此路不通**。在 `/vendor /system /odm /oem /apex /data` 全盘搜 `*rknn*` **命中 0**；`/vendor/bin` 无 `rknn_server`；`getprop` 只有 `*rockchip` 的 HAL 服务（health/light/memtrack/power-aidl/thermal），没有任何 rknn/npu 项 | 出厂安卓**没带 NPU runtime**。Toolkit2 版本口径只能改从厂商 Linux SDK 取（`4.软件资料/系统SDK/`），或烧完官方 Linux 后读 rknpu 驱动版本。详见 §3<br>**后记（2026-11-06 烧后）**：口径已从官方 Ubuntu 固件拿到——`/usr/lib/librknnrt.so` = **2.1.0**，见 §10。本行“此路不通”**只对出厂 Android 成立**，不是“RKNN 路线拿不到版本口径” |
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

**补记（2026-11-06 烧后）**：落空的第一项已在官方 Ubuntu 固件上补齐（`librknnrt.so` = 2.1.0，§10），
当年这条取证路线的欠账至此全部结清。真正的教训不是“安卓白测了”，而是
**取证要分系统做**：同一型号硬件上，Android 与 Linux 两套固件能拿到的东西完全不重叠。

---

## 4. 固件选型（已定，含拒绝项）

**选定**：`预编译固件/屏幕固件/Ubuntu/IDO_PurplePiOH2_V1B_Ubuntu22.04_HDMI4K_250907R.img.zip`

| 项 | 值 |
|---|---|
| zip | 1,886,793,196 字节 |
| 内含 `.img` | 6,163,532,419 字节（6.16 GB）+ `MD5.txt` |
| 厂商声明 MD5 | `8cdd8d825ea8976239a702b7586f3406` |
| 完整性 | `[实测]` **四处一致**：① `unzip -t` 全量 CRC 通过（57 s）② 解压后 `md5sum` = `8cdd8d825ea8976239a702b7586f3406`（15 s，6,163,532,419 B 与包内声明逐字节相符）③ 与包内 `MD5.txt`、厂商 PDF 声明相同 ④ 复制到 `D:\rkflash\` 后用 Windows `Get-FileHash -Algorithm MD5` 再算一遍仍相同。解压后 MD5 这项欠账已清 |

三个维度的取舍与理由：

| 维度 | 候选 | 选 | 理由 |
|---|---|---|---|
| rootfs | Buildroot2024 / Debian12 / Ubuntu22.04 | **Ubuntu 22.04** | 厂商《Linux 使用手册》通篇 `apt` 操作；板上 Python 3.10 与 `rknn-toolkit-lite2` 的 `cp310` wheel 口径天然对齐。Buildroot 包只有 517 MB、busybox 无包管理，装 OpenCV/V4L2 工具要交叉编译 |
| 显示变体 | HDMI4K / MIPI1200x1920 | **HDMI4K** | 功能支持列表里两者都勾选，但本项目不接 MIPI DSI 屏；MIPI 变体要去初始化一块不存在的 1200x1920 面板 |
| 实时性 | 常规 / Preempt / Xenomai | **常规**（即"屏幕固件"那组） | 板上 4K 一帧 13–22 s，瓶颈是算力不是调度抖动；Xenomai 双内核与 Python/RKNN 用户态有摩擦。点板阶段要最标准的一份 |

**拒绝项留档**：
- `Buildroot2024` 那份（517 MB）留给**交付阶段**做无人值守精简根文件系统时再评估——它其实更贴合 SDD §1.2"上电即跑"的形态，但不适合现在迭代。
- `Xenomai` / `Preempt` 两份实时固件在本轮一律不碰。若将来实测发现帧间抖动是硬约束，再单独开一条验证线。

**板级版本核对（烧前必做——本轮已关闭，判定为 V1B 可烧）**：

一开始我把设备上树的 `EVB7609 **V1** Board` 当成与 `_V1B_` 包冲突的证据，这是误读；四条证据否掉了它：

1. `[实测]` 手头 14 个包**全部标 `_V1B_`，没有任何 V1A 包**——所以问题不是"选哪个"，而是"这批包对不对"。
2. `[实测]` 厂商《Linux 使用手册》修订史：文档 V1.0–V1.2（2025-07）PCBA 版本为 **V1A**，**自 V1.3（2025-08-25）起改为 V1B** 并延续到 V1.8；《上手指南》整本（2025-09-08 创建）PCBA 即 **V1B**。
3. `[实测]` 板上出厂 Android 的内核与 uboot 构建于 **2025-09-19**（§1），晚于上面的切换点。
4. **决定性** `[实测]`：待烧镜像 dtb 内的板级串为 `Industio-EVB7609V1-VS1.0.0-250716`，与板上正在跑的出厂固件**逐字符相同**。"V1" 两边都有，它是底板名；这条实际是**兼容性确认**而非冲突。

残余风险：以上均为间接证据，**PCBA 丝印没有肉眼确认过**。但既然 V1B 包与出厂固件同源，判定可烧；若烧后点不亮，按 §5 步骤 8 走 Maskrom 恢复。

**镜像自带的分区表**（`[实测]` 从 `.img` 里 grep 出 `parameter.txt` 的 `CMDLINE: mtdparts=`，扇区 × 512 B；芯片串 `MACHINE_MODEL/MANUFACTURER = RK3576`、`FIRMWARE_VER: 1.0`、`CHECK_MASK: 0x80`）：

| 分区 | 大小 | 起始偏移 |
|---|---|---|
| uboot | 4 MiB | 8 MiB |
| misc | 4 MiB | 12 MiB |
| boot | 64 MiB | 16 MiB |
| recovery | 128 MiB | 80 MiB |
| backup | 32 MiB | 208 MiB |
| userdata | 2048 MiB | 240 MiB |
| oem | 128 MiB | 2288 MiB |
| **rootfs** | **`grow`（吃掉剩余全部）** | 2416 MiB |

含义：Linux 固件是**扁平 8 分区**，没有 Android 的 `super/dtbo/vbmeta/metadata/frp`；`rootfs:grow` 在 57.6 GiB 的 eMMC 上约得 **55 GiB**，`/opt/moireguard`、模型权重、日志轮转的空间都够。⚠️ 但 `userdata` 固定 2 GiB 且是独立分区，别把要 persist 的数据往里放——SDD 的交付路径应统一落在 rootfs。

### 4.1 已落到 Windows 本地的烧录件

```
D:\rkflash\IDO_PurplePiOH2_V1B_Ubuntu22.04_HDMI4K_250907R.img   6,163,532,419 B  (MD5 已复核)
D:\rkflash\RKDevTool_v3.32\RKDevTool_v3.32_for_window\RKDevTool.exe
D:\rkflash\DriverAssitant_v5.13\DriverAssitant_v5.13\DriverInstall.exe
```

---

## 5. 烧录操作序列（⚠️ 只能在 Windows 主机上做）

厂商《上手指南》§八「固件烧录」**通篇只有 USB 直连烧 eMMC，没有任何 SD 卡启动路径**；
《Linux 使用手册》里 TF 卡座的定位是外设存储（功能表原文写"TF卡(测试传输8G文件)"），
且警告"emmc 分区的变化可能导致 tf 卡的分区也会变化，导致无法自动挂载"。
`SDDiskTool_v1.78` 内部的 `sd_boot_config.config` 文件头自述是 "rockchip sdcard boot config file
**for factory**"，字段为 `pcba_test / fw_update / demo_copy`——它面向产线与批量升级卡，不是开发路径。

1. **Loader 驱动（本轮实测踩到并已解决）**：`adb reboot loader` 之后板子换成**另一颗 USB 设备**
   `USB\VID_2207&PID_350E\<SN>`（名 `USB download gadget`，350E 即 RK3576 的 Loader PID），Windows 报
   **Status=Error / `CM_PROB_FAILED_INSTALL`（代码 28）**。
   ⚠️ **不要把"ADB 通了"当成"能烧录"**：`PID_0006`（`rk3576_u`，ADB）的驱动确实在位且 Status OK，
   但 Loader 是不同 PID 的另一颗设备，驱动不通用。本行结论在 2026-11-06 一度被写成"不需要装驱动"，
   只因只看了 ADB 态，已更正。

   **有效修法（比厂商 GUI 省事）**，需管理员权限：

   ```
   pnputil /add-driver "D:\rkflash\DriverAssitant_v5.13\DriverAssitant_v5.13\Driver\x64\win10\rockusb.inf" /install
   ```

   跑完设备即变 `Rockusb Device`（制造商 Fuzhou Rockchip）/ Status OK / `CM_PROB_NONE`。
   `[实测]` 一个容易误会的细节：pnputil 回的是"已成功添加驱动程序包。（**系统中已存在**）"——
   即**驱动包本来就在驱动仓库里，缺的只是把它绑定到这颗设备**。所以代码 28 不等于"机上没这个驱动"，
   也不能指望 Windows 自己完成绑定。
   ⚠️ 反向坑：厂商 `DriverInstall.exe` 从 WSL 互操作直接 `Start-Process` 会**静默退出**（进程消失、
   Log 目录为空、什么也没装），别在这上面耗时间。
2. **已完成**：解压 + MD5 校验 + 落到 Windows 本地盘（路径见 §4.1）。⚠️ 原计划放 F: 盘，但开发机的 F: 卷在会话之间消失了（现仅 C/D/E），改投 D:。**绝对不要让 RKDevTool 从 `\\wsl.localhost\...` 读这 6.16 GB**——不只是慢：中途 WSL 发行版一旦被挂起，读流断在半路就是半砖（虽然 §5 步骤 8 的 Maskrom 能救回来，但没必要冒）
3. **免按钮进 Loader**（本轮实测可用）：在还跑着 Android 时直接 `adb.exe reboot loader`，十几秒后 Windows
   就枚举到 `PID_350E`。只有进不了 Android / adb 不通时，才回到下面的 Recovery 手法。
   `RKDevTool.exe` → **断开板子电源** → Type-C 接板子 **OTG 口** → **按住 Recovery 键不放** → 上电，约 2 s 后应显示"发现一个 LOADER 设备"
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
| 开发机会漂移（⚠️ 重要） | `[实测]` 两次会话之间 WSL 重启过：根文件系统从 `overlay 98 G` 变成 `/dev/sdd 1007 G`、`/mnt/f` 整卷消失、沙箱内 `/tmp` 变只读、**上一轮 `mutool` 抽出的 PDF 文本临时文件全部丢失**，且 `mutool` 版本变了（`-F` 现在必须带格式参数）。教训两条：**一次性数据当场写进文档，别指望临时文件还在**；抽 PDF 文本改用 `python3 -c "import fitz"`（PyMuPDF 1.28 在位），但 `docs/vendor/purplepi-oh2/*.pdf` 是唯一可信源 |
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
| DDR 具体类型（LPDDR4 还是 4X） | 仍**未坐实**，但数据变好看了 | Linux 态 `dmc/available_frequencies` = **534 / 1320 / 1968 / 2736 MHz**（§10）。⚠️ 不拿频率台阶反推类型，那是越界推断；类型仍只能从 SDK dts 或客服口径拿 |
| ~~RKNN 整链版本路线~~ | **已关闭（§13.1）**（本行历史上已“关闭→重开→再关闭”一次） | 板上 runtime 实测是 2.1.0，但 **PyPI 上 `rknn-toolkit2` 与 `rknn-toolkit-lite2` 都没有 2.1.0 发布版**，所以“钉 2.1.0”不是一个可直接执行的指令。两条候选路线与推荐实验见 §11.4；**实验已跑完（§13.1）：转换端 2.3.2 产物能被板上 2.1.0 runtime 加载并推理成功，A/B 两条都不必要、不需替系统库** |
| ~~解压后 `.img` 的 MD5 比对~~ | **已完成** | 四处一致，见 §4；含 Windows 侧 `Get-FileHash` 复核 |
| ~~V1A / V1B 板级版本~~ | **已完成** | 判定 V1B 可烧（烧后系统正常跑起来，反证判定成立），证据链见 §4；PCBA 丝印仍未肉眼确认 |
| ~~执行烧录~~ | **已完成（2026-11-06）** | 官方 Ubuntu 22.04.5 已跑起来，板端实测见 §10。【擦除所有】未碰，SN 存活 |
| **首帧实测** | **阻塞：摄像头未接** | 软件栈就绪（NPU runtime 2.1.0、V4L2 节点齐、`v4l2-ctl` 可用），但 IMX415 在 I2C 上不应答（§10 摄像头行）。需要：插好模组到 **J19 或 J25**（§2 行 46：插 J20 只会得到偏绿画面）→ 装 `rknn-toolkit-lite2` + numpy/opencv（板上现在连 numpy 都没有）→ 先 warmup 再计时，复核 "120–200 ms/块、4K 一帧 13–22 s" |
| ~~网络接入~~ | **已解决（走 WiFi）** | 板上其实有 WiFi 模组。已关联 5G、拿到 DHCP、依赖已装齐，实测见 §11 |

---

## 10. 烧后实测（官方 Ubuntu 固件，2026-11-06）

烧录成功，出厂 Android 已被替换。以下全部 `[实测]`。

| 项 | 实测值 | 意义 / 对既有文档的影响 |
|---|---|---|
| 系统 | Ubuntu 22.04.5 LTS，kernel `6.1.99` SMP PREEMPT，**构建于 2025-09-07 14:23 CST**，hostname `industio` | 内核构建日期与固件号 `250907R` 对得上，确认烧的就是选定的那个包 |
| USB 身份 | `adb devices -l` → `product:occam model:Nexus_4 device:mako` | Rockchip **Linux** 镜像里 libcomposite adbd 的默认 ID（Android 态是 `rk3576_u`）。**以后判别“板上跑的是什么系统”看这个字段，不用看界面** |
| adb 权限 | `id -un` → **root**（无需 `$ADB root`） | 部署能直写系统路径；但**交付时这是个安全项**，见 §10.3 |
| SN | `98fed70982ab4c63` **跨刷机存活** | 上一轮担心的“写号丢失”没发生，无需重跑 RKDevInfoWriteTool |
| RAM | `free -m` total **3895 MiB** | “4 GB”这个 SKU 参数**首次拿到板端实测**（OS 可见值，与标称 4 GB 的正常差额）；SDD §1.1 的 4G 不再只靠订单 |
| 分区 | 扁平 8 分区，与镜像 `parameter.txt` 逐字节相符：p1 4 MiB / p2 4 MiB / p3 64 MiB / p4 128 MiB / p5 32 MiB / p6 **2 GiB → `/userdata`** / p7 128 MiB → `/oem` / p8 **59,332,607,488 B = 55.25 GiB → `/`** | §4.1 从分区表推出的“rootfs 约 55 GiB、userdata 固定 2 GiB”**命中**。落盘约束成立：要 persist 的数据写 rootfs（`/opt` 就在 p8 上，余 48 G），别往 `/userdata` 写 |
| eMMC | `lsblk` 总量 61,865,984,000 B —— 与 Android 态 `mmcblk0/size` 完全相同 | 容量不受系统切换影响；换系统不影响之前的字节换算 |
| **NPU runtime** | **`/usr/lib/librknnrt.so` = `2.1.0 (967d001cc8@2024-08-07)`** | 这个版本号本身成立；但“所以转换端钉 2.1.0”这一步在 §11 被推翻（PyPI 无 2.1.0 发布版）；整链版本路线已在 §13.1 判定（转换端 2.3.2 + 板端 2.1.0 可互通）|
| NPU 调频 | 档位 300–950 MHz、governor `rknpu_ondemand` —— **与 Android 态一字不差** | §2 的“测延迟前必须 warmup”在 Linux 上同样成立，**不是刷机可绕的开箱问题** |
| CPU | 8 核，`policy0` / `policy4` governor = **`schedutil`** | 与 Android 态的 governor 不同，所以**跨系统对比延迟数不能直接对照** |
| 摄像头 | **驱动在位但物理未接**：`imx415` 驱动已注册（`driver version 00.01.08`、`detect imx415 lane 4`），设备树 `i2c@2ac80000/imx415@1a`(dphy1)、`i2c@2ac90000/imx415@1a`(dphy0)、`ov13855@36`(dphy3)；`5-001a` probe 报 `Failed to get power-gpios`；对 `6-001a` 强制 bind 读到 **`Unexpected sensor id(000000), ret(-5)`** | -5 = EIO、chip id 全 0 ⇒ **I2C 上没人应答**，即模组没插 / 插错座 / 排线反。V4L2 栈本身正常（73 个 `video*`、`media0-8`、`v4l2-ctl` 可用、`video0` = `stream_cif_mipi_id0`）。**首帧实测的阻塞项是硬件接线，不是软件**。另：设备树里两条 imx415 并存，印证 J19/J25 都走 IMX415 |
| 板上 Python | 3.10.12 + pip 22.0.2，但 **`numpy` 与 `cv2` 均未安装**，`rknn-toolkit-lite2` 也未装 | cp310 与 lite2 wheel 口径对齐 ✓（§4 选型理由成立）；但这意味着**部署前还有一层依赖要装**，不能指望镜像自带 |
| 工具链 | `v4l2-ctl` 在 `/usr/bin`；`/dev/video-camera0`、`video-dec0`、`video-enc0` 均存在 | 取帧与硬编解码自测可直接在板上跑，不必先交叉编译 |

### 10.1 一次探测的副作用（照例交代）

为分清“驱动没编进来”还是“探针失败”，我往 `/sys/bus/i2c/drivers/imx415/bind` 写过 `6-001a`。
该 probe 因读不到 chip id 而失败，**绑定并未建立**（驱动目录下仍只有 `bind/uevent/unbind`），
系统状态与探测前一致，无需回滚。

### 10.2 仍拿不到的东西

DDR 类型（只有频率台阶，不断言）、以及任何需要联网的验证（板上无 IP，开发机也没下 SDK）。

### 10.3 交付前的安全项（新增，别漏）

`adb shell` 直接给 root，且 adbd 在开机时就已在跑（本次未手动启动就能连）——演示/交付环境里
等于敞开的大门。量产或外场交付前需要关掉它；具体启动单元本次未查，定位时跑
`systemctl list-units | grep -i adb`。这条与 §5 的“烧录即抹盘”一样属于出厂动作，不是可选项。

---

## 11. WiFi 与板端依赖（2026-11-06 第三轮）

本板**确实带 WiFi 模组**，所以“没插网线就不能联网”是个错判断（它已记在 §9）。
以下全部 `[实测]`。**注意：本文档不记录 PSK**，连任何接入点都不要把密码写进仓库。

### 11.1 无线链路本身

| 项 | 实测值 | 说明 |
|---|---|---|
| 芯片 / 驱动 | **Broadcom/Cypress BCM43456**，走 **SDIO（`mmc2:0001:2`）+ `dhd` fullmac**；固件 `wl0: May 30 2023 version 7.45.96.180`；MAC `10:a5:62:1a:11:00` | 不是常见的 `brcmfmac` 而是 Rockchip 用的全宏 `dhd`；排查时看 `[dhd]` 前缀日志而不是 `brcmfmac` |
| 接入 | `Xiaomi_432`，WPA2-PSK，**关联在 5G：freq 5180（ch36）、signal −50 dBm** | NM 自动选了 5G 那个 BSSID（`...:5F:9B`）而不是 2.4G（`...:5F:9A`），不用干预 |
| 持久化 | `nmcli device wifi connect` 自动生成了 profile（UUID `94189ca0-…`）并开机自连 | 重启后不必重连，交付形态里这算“已配好网络” |
| IP / DNS | `192.168.31.53/24`，网关与 DNS 均 `192.168.31.1`，默认路由 `via 192.168.31.1 dev wlan0 proto dhcp metric 600` | `GENERAL.STATE: 100 (connected)` |
| 外网 | 网关 ping 0% 丢包 / 7.3 ms；`223.5.5.5` 0% 丢包 / 26 ms；`apt-get update` 退出码 0，IPv4 解析正常（`getent ahostsv4` → 101.6.15.130） | apt 源可用，因此“板端依赖必须离线预置”这个前提已不再成立（但交付镜像仍应预装，不能靠现场联网） |
| 反向可达 | 开发机 WSL 有 `eth3 192.168.31.6/24`（**与板子同网段**），ping 通，拿到 `SSH-2.0-OpenSSH_8.9p1` 横幅 | `build/README.md` 的 scp/ssh 部署路线从此可用；板上 `sshd` active 于 `0.0.0.0:22` |

### 11.2 CLM blob 缺失——查清了，不阻塞但非零代价

启动日志报 `Direct firmware load for /clm_bcm43456c5_ag.blob failed with error -2`。盘上实测：
`/lib/firmware/` **有** `fw_bcm43456c5_ag.bin`（固件本体，所以驱动能起来）和 `nvram_AP6275P.txt`，
但 **没有 `clm_bcm43456c5_ag.blob`**——只有 `clm_bcm43013c1` / `clm_bcm43752a2` / `clm_syn43756b0` /
`clm_syn4381a0` 四个**别的芯片**的 CLM。所以是**厂商打包漏了这个芯片的法规表**，不是我们环境坏了。

后果（已看到，不是推测）：`iw reg get` 返回 **`country 00: DFS-UNSET`**（世界域），5G 各段均标
**PASSIVE-SCAN**。本次连 ch36 仍成功，但“能过”不等于“无风险”：换信道 / 连新 AP 时可能变慢或连不上。
**我没有对应 blob 可补，也不拿别的芯片的 blob 冒充**；可选缓解是设国标域（待验证，见 §12）。

### 11.3 板端依赖已装（这就是联网的直接产出）

| 装了什么 | 版本 | 来源 |
|---|---|---|
| `python3-numpy` | **1.21.5** | apt |
| `python3-opencv` | **4.5.4** | apt（`import cv2` 已验） |
| `iw` | — | apt，为了拿到 §11.2 的法规域证据 |
| `rknn-toolkit-lite2` | **2.3.2** | pip，PyPI 有 `cp310-manylinux_aarch64` wheel，板上直接装成 |

这四项补齐后，§9 里“装 `rknn-toolkit-lite2` + numpy/opencv”那个前置已不再是欠账。

### 11.4 推翻本会话上一步的结论：版本链不是“钉 2.1.0”那么简单

上一步我把板上 `librknnrt.so` = 2.1.0 直接推成“转换端钉 `rknn-toolkit2==2.1.0`”，
本轮实测证明**这个推论不可执行**：

| 实测 | 结果 |
|---|---|
| PyPI `rknn-toolkit-lite2` 已有版本 | **2.3.0 / 2.3.2 / 2.5.0——无 2.1.0** |
| PyPI `rknn-toolkit2` 已有版本 | **2.2.0 / 2.2.1 / 2.3.0 / 2.3.2 / 2.5.0——无 2.1.0** |
| lite2 wheel 自带 `librknnrt.so` 吗 | **不自带**（`find` 无结果，`ldd` 也不依赖），它在运行时 dlopen 系统的 `/usr/lib/librknnrt.so` |
| 2.1.0 runtime 的 API 符号 | `rknn_init`、`rknn_create_mem` **均存在** → 无 dlsym 缺口（但这不等于全兼容） |
| 能否不装模型就判定成败 | **不能**。`get_sdk_version()` 返 `None` 并报 “Runtime environment is not inited”，版本只在 `init_runtime` 后才能读 → **必须有一个真 `.rknn` 才能终证** |
| 内核侧 | `RKNPU driver: v0.9.8`（升级 runtime 时的真前提，本次未拿官方配套表坐实，**不作断言**） |
| 取件通道 | `github.com` 从板子直连**超时（HTTP 000）**，`api.github.com` 返 200；开发机直连也不通，但**走本地代理 `127.0.0.1:7897` 返 `200 Connection established`** → 老 wheel / runtime 库可经开发机取进板 |
| 本地厂商资料 | D 盘及工作区**找不到任何 rknn wheel/deb**（原 `4.软件资料` 在已消失的 F: 卷上） |

**候选两条路线**（当时待定；§13.1 实测后**两条都不必要**）：

- **A（整链升 2.3.2）**：转换端 `rknn-toolkit2==2.3.2`（PyPI **有** cp310/cp312 x86_64 wheel，不需 Docker tag 推测），板端 lite2 2.3.2（已装），代价是要把 `/usr/lib/librknnrt.so` 升到 2.3.2——**属替换厂商系统库，须先备份、可改名回退**，且要实板验证与 driver 0.9.8 的配合。
- **B（保持 2.1.0）**：不动系统库（厂商验证过的组合），代价是两个 2.1.0 wheel 都得从 GitHub release 绕道取（经开发机代理），且转换端老版本的 x86 wheel 可得性未证。

**推荐的判定实验（一次跑完就能定路线，而且本来就该做）**：用 2.3.2 在 x86 上把 `SFR_best.pth → ONNX → .rknn` 转出来，**先不改板上 runtime**，直接推到板上跑 `init_runtime`：
成功→ 维持 2.1.0，路线 B 自动成立且不用取老 wheel（最便宜）；失败（典型为模型版本不匹配）→ 再按路线 A 升级 librknnrt（已备份，可回退）。
无论哪条，都不影响“必须 warmup 再计时”和“`--target rk3576`”这两条已定结论。

> **本实验已执行，结论见 §13.1：成功。两个路线都不需要——既不用升系统库，也不用去取 2.1.0 的老 wheel。**

---

## 12. 本轮留下的待决项

| 待决 | 为何重开 | 谁能定 |
|---|---|---|
| ~~RKNN 整链版本路线 A / B~~ | **已关闭（§13.1）**：2.3.2 转出的模型能被板上 2.1.0 runtime 加载并推理成功，A/B 两条路线都不必要 | 实验已定；但升级 runtime 仍不属于可自行决定的范围，因为已不需要 |
| ~~FRM 硬路由上不了 NPU~~ | **已关闭（§14.2–§14.3）**：导出侧 mask-sum 静态化（逐 bit 等价）+ onnxslim 常量折叠，真模型已经转成 .rknn 并在板端 NPU 跑对 | 链已通；但 §13.3 第二层（路由语义）与 §14.1 对第一层的误归因仍是教训 |
| **router 无判别力（模型层，新提升为一级风险）** | §13.3：softmax 接近均匀（top1-top2 仅 0.018–0.054），60 个真实切片里 14 个与 dummy 路由不同；静态化只保证“图上能算对”，不保证“选得对” | 需重训 router（辅助监督 / 负载均衡），属训练目标，要用户定 |
| **单块延迟 3.0 s / 整帧 96.6 s（fp16）** | §14.4 实测的 2.83–3.15 s 是**单块 512×512**，§15.1 产品路径实测整帧 2275×1279（32 块）**96.6 s**；我此前一直把单块当整帧写，低估了一个数量级。与“实时滤摩尔纹”叙事直接冲突 | 要么改口径（单帧离线 / 降分辨率 / 滑窗抽帧），要么先查清“算子是否落在 CPU 上”（折合仅 0.12 TFLOP/s）；先要用户确认可接受的帧率 |
| **判定层无标定：干净图也报“有摩尔纹”** | §15.4 实测：摩尔纹图 coverage 0.9973，两张干净截图 0.9991 / 0.9815——现配置下布尔恒为 True。已排除预处理口径、tile 尺寸、转换误差三个归因，剩下两个候选：模型精度本身、负样本的定义 | 需用户定“干净”指什么、并用真实屏摄负样本标定双门限（§15.4 已给出当前唯一已知的可行窗口） |
| ~~平台误判为鸿蒙~~ | **已修（§15.2）**：`/system/etc` 在厂商 Ubuntu 固件里本来就存在，导致相机被装配成未实现的鸿蒙适配器 | 已进 `tests/smoke_board_pipeline.py` §9 钉住；真 OH 硬件到位后用 `MOIREGUARD_PLATFORM=ohos` 显式跑 |
| **真实屏摄校准集缺失** | int8 量化需要 100–200 张真实屏摄图（SDD §6 风险 #7），现在只有 4 张且**无一张是摄像头采的**；本轮靠 fp16 不量化绕开了 | 摄像头接入后才能采集（§9）；接入后注意校准集要落成归一化 .npy（§14.4 预处理口径） |
| **摄像头物理接线** | §9、§10；软件侧已无卡点 | 只能接实线，接完告诉我插的哪个座 |
| 法规域是否设为 `CN` | §11.2 的 `country 00` + PASSIVE-SCAN 是 CLM 缺失的真实代价 | 可试 `iw reg set CN` 后回读 `iw reg get`；但 dhd fullmac 是否听 cfg80211 域设置**本次未证**，试了才知道 |
| 交付镜像里依赖怎么固定 | 本轮靠 apt/pip 现场装（能装是因为有网），但验收口径是“上电即跑、不依赖联网” | 应写进 `build/` 打包流水线（SDD §1.2）；具体形式待定 |
| WiFi 凭据本身 | 本文档**故意不记 PSK** | 交付时由现场配置，或写成不含密码的 provision 脚本 |

---

## 13. 模型转换实验：版本链定了，但真正的阻塞是 FRM（2026-11-06 第四轮）

本轮把 §11.4 欠的转换实验跑完了。结论：**版本不是问题，模型结构才是问题**。

### 13.1 版本链判定：A / B 两条路线的预测各对一半

注意不要误读：“路线 A”在 §11.4 里 **含“必须升 `librknnrt.so`”这个预设**，而实验结果是这个预设不成立。
真实落点是 **A 的转换端（toolkit 2.3.2）+ B 的板端 runtime（保持厂商 2.1.0）**：A 预计要替系统库——不必；B 预计要去 GitHub 取 2.1.0 老 wheel——也不必。

用一个能转成功的最小 Conv 模型（`build/probes/Conv.onnx` → `build/models/probe_conv_fp16.rknn`）推到板上跑，
**板上 `/usr/lib/librknnrt.so` 一个字未改**（实验脚本：`build/_probe_board.py`）：

| 实测 | 结果 | 推论 |
|---|---|---|
| 模型头回读 | `version: 6, toolkit version: 2.3.2 (compiler e045de294f@2025-04-07), target: RKNPU f2, target platform: rk3576, framework: ONNX, NCHW, static_shape` | `--target rk3576` 确实写进了产物，这个不是口头约定 |
| 版本检查 | `W RKNN: RKNN Model version: 2.3.2 not match with rknn runtime version: 2.1.0` | **只是 warning，不是拒绝**——这是 §11.4 那个“必须真模型才能定”的答案 |
| `init_runtime(core_mask=AUTO)` | **ret = 0** | 旧 runtime 能加载新产物 |
| `get_sdk_version()` | `API: 2.1.0 (967d001cc8@2024-08-07T19:28:19)` / `DRV: 0.9.8` | 独立复证了 §10 靠 `strings` 得到的 2.1.0，也复证了 driver 版本 |
| `inference()` | ✅ 返回 shape=(1,3,8,8)，sum=22.5409 | 整链跑通，不只是加载 |
| lite2 推理接口名 | `RKNNLite` **没有** `.forward()`，只有 `.inference()` | 我第一次写错了，不是版本不兼容（不要把 API 名当成兼容性证据） |

所以：**转换端用 PyPI 能装到的 2.3.2，板端 runtime 保持厂商 2.1.0，不动系统库**。但这是**最小模型**上的验证；SFRNet 本身的产物仍未得到（见 13.3）。

### 13.2 x86 转换环境的三个坑（都可复现，写进 `build/README.md`）

环境：仓库内 `.venv-convert`（`/usr/bin/python3.12` 建，**不是** `GpuTorch`，没往你的训练环境装任何包）。磁盘余 738 G。

| 坑 | 现象 | 处置 |
|---|---|---|
| `setuptools>=81` 删了 `pkg_resources` | `RKNN()` 构造即报 `No module named 'pkg_resources'` | `pip install "setuptools<81"`（toolkit2 没钉上界，这是它的打包问题） |
| `onnx>=1.16` 删了 `onnx.mapping`，而 **cp312 上 onnx≤1.15 根本没有二进制 wheel** | `module 'onnx' has no attribute 'mapping'`；pip 会退回源码构建并失败 | 只能给这个**一次性 venv** 挨个补 shim：`site-packages/onnx/mapping.py`（提供 `TENSOR_TYPE_TO_NP_TYPE`，8 种 dtype 已逐项校对）+ 在 `onnx/__init__.py` 末尾挂一行 `import onnx.mapping`（rknn 只 `import onnx` 不拿子模块属性）。**产品代码不受影响** |
| 训练环境依赖没声明全 | `No module named 'app'` → `No module named 'PIL'` | `export_model.py` 补了 `sys.path.insert(仓库根)`；`pillow` 补装。**注意：`build/README.md` 里写的 `python build/export_model.py …` 本来直接报错**，同一类“文档没在代码侧落实”的缺陷第二次出现 |

另外：`rknn-toolkit2` 自身依赖 torch，装 2.3.2 会连带拉进 torch 2.4.0(cu12) / onnxruntime / opencv / numpy 1.26——**不需要另装 CPU torch**。

### 13.3 真正的阻塞：FRM 硬路由（两层，第二层更危险）

> ⚠ **本节第一层的归因后来被证明不完整**：NonZero 确实被 toolkit 拒收，但整图 SIGABRT 的主因另有其他
> （静态化把 NonZero 清零后仍 abort，且崩得更早），真正的炸点在 §14.2 才找到。保留本节原文是为了记“先报明确错的不等于唯一的错”。

第一层——**toolkit 直接拒收**。`sfrnet.onnx`（570 节点，`load_onnx` 返回 0，说明解析层全过）在 `build()` 的
`fold_constant` 跑到约 30% 时 **SIGABRT（exit 134）且无任何 Python 栈**（无 gdb，faulthandler 也没吐）。用最小程序（`build/_probe_ops.py`）逐算子隔离定位：

| 探针算子 | 结果 |
|---|---|
| `Conv`（对照） | ✅ build OK，能导出 .rknn |
| `ArgMax` | ✅ build OK |
| **`NonZero`** | ❌ `The input 0 of NonZero('/NonZero') need to be constant! It will cause the graph to be a dynamic graph! Remove NonZero('/NonZero') manually and try again!` |

来头清楚：`upstream/sfr/FRM.py:88-99` 的 `idx = (preds == i).nonzero(as_tuple=True)[0]` + `if len(idx) > 0` + `block[idx]` / `output[idx] = …`。
ONNX 算子直方图完全对得上：`NonZero=4 / ScatterND=4 / Equal=4 / ArgMax=4 / Range=4`，而 FRM 实例只有 1 个（`fission_module`）、每帧调用 4 次→ **每个 block 只有一条专家分支被 trace 进图**。

第二层——**即使绕过第一层，导出的图也是错的**。这不是推论，是实测（`build/_probe_routing.py`）：

| 实测 | 结果 |
|---|---|
| `export_model.py` 自带的 torch-vs-onnx 自检 | `rel = 6.95e-06 ✅ 通过`——**但自检用的就是导出时那个 dummy**，只验证了“dummy 那条路由” |
| 换 5 种输入（含两个不同随机种子）再对比 | 仍全部 `rel≈5–7e-06`、相关系数 +1.0000 → 当时我判定“路由冻结碰巧无害” |
| 直接记录 router 的 argmax | 5 种输入均返 `[1,1,1,1]`——router 对所有 block 都选专家 1 |
| **60 个真实 512×512 随机切片**（四张图×15，含摩尔纹照片与两张屏摄） | 命中分布 `{专家1: 196, 专家3: 44}`，**14/60 个切片的路由与 dummy 不同** |
| router 的 softmax | `[0.223, 0.283, 0.249, 0.245]` 等，top1-top2 差仅 **0.018–0.054**（四类均匀分布是 0.25） |

⇒ 两条结论必须分开记：
1. **部署层**：当前 ONNX 对 **14/60 的真实切片语义错误**（torch 会走专家 3，图里只有专家 1）。之前 5 个输入“全对”是因为它们整体被路由到 1，样本不够。
2. **模型层**：router 输出接近均匀分布，基本没判别力——**“按 block 特征动态选专家”这个创新点在当前权重下不成立**，这对答辩/论文是个直接风险，与部署无关。

### 13.4 出路（需用户定，本轮未动模型代码）

| 方案 | 做法 | 代价 / 风险 |
|---|---|---|
| **A. mask-sum 静态化（建议）** | 把 `process_block_hard_routing` 改为“4 个专家全算，乘 one-hot(preds) 掩码后求和” | **推理语义**与原硬路由逐字等价，故现有 `SFR_best.pth` 不用重训就能转；NonZero/ScatterND 消失。代价：4 个小分支都跑（仅 3→16→32 通道，重活在 SAFM，增量有限）；⚠ 但若这个写法同时用于**训练**，梯度路径会变（原靠 STE），需单独验证 |
| B. 改软路由 | 直接用 probs 加权（去掉 argmax） | 图更简单、梯度更健康，但改变了“硬路由”叙事，且**需重训** |
| C. 只修训练 | 加辅助监督/负载均衡把 router 训起来 | **不能单独解决部署**：NonZero 仍会被 toolkit 拒收。但它必须做，否则 router 训不熟，A 只是让错误的选择能跑在 NPU 上 |

我的建议：**A + C 并行**——A 把链跑通（不重训，本周就能上板），C 让路由真正起作用（下轮训练目标）。要哪个先动，等你拍。

---

## 14. 转换链打通：静态化 + 常量折叠，真模型上板跑对（2026-11-07 第五轮续）

§13.4 的方案 A（导出侧 mask-sum）已落地，而且**链最终跑通了**：`SFR_best.pth` → `sfrnet_static.onnx` → 常量折叠 → `sfrnet_*_fp16.rknn` → 板端 NPU，
输出与 onnxruntime 参照**相关系数 1.0000、最大相对误差 0.0059**。但中间推翻了两个我自己的判定，这才是本节的主要内容。

### 14.1 先推翻自己：单算子探针全部“无罪”

把 `build/_probe_ops.py` 的算子探针从 5 个扩到 12 个（重点补上静态化新引入的 `OneHot`、以及 `Pad(reflect)` / `ConstantOfShape` / `Shape→Gather` 等），逐 case 独立子进程跑：

| 探针 | exit | 结论 |
|---|---|---|
| Conv / ArgMax / ScatterND / PadReflect / OneHot / ShapeGather / Resize / GlobalPool / SoftmaxGemm | 0 | 9 个都能 `rknn building done` —— **单算子里找不到 abort 的元凶** |
| NonZero | 1 | 仍是那条明确拒收（`need to be constant! … dynamic graph`） |
| RangeEqual / ConstantOfShape | 1 | `ValueError: All outputs of model are constants` —— **探针自身写错**（输出跟输入无关），是伪阳性，不是算子被拒 |

顺带一个工具性 bug：`make()` 没有逐 case 容错，`ScatterND` 探针在 batch=1 上索引越界抛异常，**直接断了后面 9 个 case**，上一轮那句“只跑出 3/12”是这么来的。补了 per-case `try/except` 后 12/12 导出。

⇒ 定性修正：§13.3 把 abort 归到 NonZero 是**不完整的**；NonZero 只是“一个会被明确拒绝的算子”。整图炸点必须换手段定位。

### 14.2 前缀二分才是真正的定位手段（`build/_probe_bisect.py`）

思路：按拓扑序把 `sfrnet_static.onnx` 截成前 k 个节点，边界张量（被后半图消费、依赖输入、浮点）当输出，逐刀问 toolkit “这前半图能不能 build”。在“可切刀序列”上二分找**第一个 abort 的 k**。

| 实测 | 结果 |
|---|---|
| 可切刀数 | 1324 个节点里 1317 刀可切（范围 8–1324） |
| 单刀耗时 | 约 5 s（abort 崩得很早，二分十几刀就收敛） |
| 二分轨迹 | `666✘ → 336✔ → 501✘ → 418✔ → 459✘ → 438✘ → 428✘ → 423✘ → 420✔ → 421✘` |
| **炸点** | **420 节点安全，加上第 421 个就 abort** —— 节点 #421 = `Slice /fission_module/Slice_1` |
| 为什么是它 | 它的输入 #415 是 `Pad(mode=reflect)`，而 pads 值走了 `Transpose(perm=[1,0]) → Reshape → Cast` 链；ONNX 形状推断不做常量求值，于是 Pad/Slice 的输出全是 `unk__80..83` |

一个坑必须记：`subprocess.run` 对 SIGABRT 给的 `returncode` 是 **-6**，不是 shell 的 134。我第一版按 `== 134` 判，得出“全图没复现 abort，前提不成立”的错误结论，差点把整条二分推翻重来。

接着做了全图符号形状普查（`build/_probe_shapes.py`）：**1324 个张量里 944 个含符号维度**，按生产者聚类是 Conv 269 / Relu 215 / Concat 150 / …——一个 Pad 的未推出来的形状把下游整个网络都污染了。所以这不是“FRM 一处小毛病”，而是**导出产物整体没有静态形状**，toolkit 在这种图上会静默 abort（不报算子名、不进 `fold_constant`）。

### 14.3 缺的一步是常量折叠：`onnxslim`（已焊进流水线）

图本身其实是全静态的（导出时没给 `dynamic_axes`），只是 ONNX 的形状推断不会算。Rockchip 官方转换流程本来就建议接一个 simplifier，于是：

| 步骤 | 实测 |
|---|---|
| `python -m onnxslim sfrnet_static.onnx sfrnet_sim.onnx` | 3.5 s；`Shape 14→0`、`Transpose 1→0`、`Slice 49→38`；模型 29.75 MB |
| 折叠后符号维度张量 | **944 → 8**（只剩 `fission_module` 里 4 个 `OneHot` + 4 个 `Cast` 的 depth 维） |
| `rknn.build(do_quantization=False)` | **1 m 47 s 跑完，exit 0**，`rknn building done` → `sfrnet_sim_fp16.rknn` 50,927,222 B |
| `E RKNN: Unkown op target: 0` ×5 | 出现但**不致命**：产物能加载、板端结果与参照一致（§14.4），所以这条错误级日志在当前图上属于良性，不要拿它当失败证据 |

已接入 `build/export_model.py`：`rknn` 子命令会先数符号维度，非 0 就自动折叠（`--no-simplify` 留给取证）；`sfrnet_static.onnx → sfrnet_wired_fp16.rknn` 全流程重跑一次，板端数字与手工产物一致。环境多一个包：`.venv-convert/bin/pip install onnxslim`（实测 0.1.99）。

### 14.4 板端真模型首跑，顺带把两个契约打死

工具：`build/_probe_eval_x86.py`（x86 侧准备输入 + onnxruntime 参照，真屏摄图 `build/test_moire.jpg` 缩到 512）
＋ `build/_probe_board_run.py`（板端加载 .rknn、两种布局各跑一次、当场比参照）。

| 实测 | 结果 |
|---|---|
| `load_rknn` / `init_runtime` | ret 0 / ret 0；runtime 仍是 `API 2.1.0 / DRV 0.9.8`，**没动系统库** |
| 输出 | shape `(1, 1, 512, 512)`，raw logits（契约 O1 成立，没被偷偷加 sigmoid） |
| **NHWC float32 喂法** | **最大相对误差 0.0059，相关系数 1.0000 ✅** |
| NCHW 喂法 | 相对误差 0.8724，相关系数 0.0321 ❌（对不上，但不报错——静默错位，最危险的那种） |
| 单帧耗时 | 2.83–3.15 s / 512×512 fp16（含 Python 侧开销） |

⇒ 两个判定：
1. **SDD §4.1 要点 1 的“RKNN 吃 NHWC”成立**，`RknnRuntime.infer` 的 permute 是对的。我一度因为模型头写着 `framework layout: NCHW` 而怀疑它——那个字段指的是**源框架布局**，不等于运行时输入布局，别再拿它当输入契约证据。
2. **但输出侧已经是 NCHW**，而 `RknnRuntime.infer` 里 `if out.ndim==4 and out.shape[1]==1: transpose` 恰好命中单通道情形，会把对的 `[1,1,H,W]` 摇成 `[1,H,W,1]`。已改为只在 `shape[1]!=1 and shape[-1]==1` 时才转，`warmup()` 也统一成 NHWC；`build/export_model.py` 的 `verify()` 里同一处逻辑同步修了。

**预处理口径的坑（本轮唯一的“完全错误”产出）**：第一版把 ImageNet 的 `MEAN=[0.485,…] / STD=[0.229,…]` 直接填进 `rknn.config`，板端三种喂法输出量级全错（参照 mean=-9.05，板端拿到 -68 / -13836 / -14076）。原因就写在 config 那行警告里：**rknn 的 mean/std 是直接作用在你喂进去的张量上的，不会帮你 /255**。既然契约 C1 要求归一化在调用侧做完，转换时就应该烘**恒等**预处理（`MEAN_BAKED=[0,0,0] / STD_BAKED=[1,1,1]`）；int8 路径的校准集也随之改成“用同一套预处理落成归一化 .npy 再列进 txt”，否则校准分布与真实输入不是同一个域。那行警告不是噪音，是结论。

### 14.5 还没闭环的（别把本节读成“全部搞定”）

- **int8 还没转过**：没有真实屏摄校准集（SDD §6 风险 #7 的红线没动），当前只有 fp16 产物。
- **延迟**：本行的“单帧 2.9 s”说法已被 §15.1 修正——那是**单块**，整帧（32 块）实测 96.6 s。要么改口径，要么量化 + 降分辨率 + 滑窗抽帧，这个得先定目标帧率。
- **router 判别力不足（§13.3 第二层）完全没被本节解决**：静态化只保证“4 个专家都在图上、按 argmax 选的那个乘掩码”，选得对不对是训练问题。§15.4 又给出一个更前面的实测：就算路由全对，**当前阈值下干净图也过不了“无摩尔纹”**。
- `OneHot` 的 depth 维仍是符号的（8/2010），目前不影响产物；如果哪天炸了，从这里查起。

---

## 15. 产品代码路径上板：链在整帧层面也等价，但挖出两个装配缺陷（2026-11-07 第六轮）

§14 的验证全走我自己的探针脚本（直接 `RKNNLite.inference()`）。探针能跑对只说明**运行时边界**干净，
不说明 `app.main → DetectionEngine → RknnRuntime` 这条真实路径能用。本轮把 app 包推上板走产品路径，
**一个命令就炸了**——而这正是它的价值。

### 15.1 首跑：FATAL，不是推理失败而是装配失败

```bash
A=Tools/ADB工具/adb_win/adb.exe            # 设备号 98fed70982ab4c63
$A push app /root/app && $A push configs /root/configs && $A push samples/moire_photo.jpg /root/
$A shell 'cd /root && python3 -m app.main --backend rknn --model /root/sfrnet_wired_fp16.rknn \
          --camera /root/moire_photo.jpg --frames 2'
```

| 阶段 | 实测 |
|---|---|
| 首次尝试 | `EXIT=1`，stdout 只有 rknn 自己的日志；真相在 `/var/log/moireguard/moireguard.log`：**模型加载与预热完成** ✅ 之后 `摄像头打开失败（第 1/3 次）: 鸿蒙相机 Kit 适配待 OH 镜像方案确认后实现` → `FATAL: camera_open_fail×3` |
| 修后重跑 | `EXIT=0`；`BOOTING 完成: READY, 耗时 4106ms`；`连通性自检通过: 2275x1279x3` |
| 逐帧结果 | `frame=file-000002 has_moire=1 coverage=0.997290 tiles=32 total_ms=96556`；`frame=file-000003 … coverage=0.997290 tiles=32 total_ms=96615` |
| 日志落点提醒 | 板端 `LinuxDeviceIO` 写文件不写 stdout，**产品路径出错时 stdout 是干净的**，不看 `/var/log/moireguard/moireguard.log` 就会拿到一个无信息量的 EXIT=1 |

⇒ `RknnRuntime` 的加载 / 预热 / NHWC 输入 / NCHW 输出修正均已在**产品代码**上实跑验证（不只是探针）。

### 15.2 两个真缺陷（均已修 + 已钉用例）

| 缺陷 | 证据 | 修法 |
|---|---|---|
| `detect_platform()` 把这块板判成 **ohos** | 板上是 `Ubuntu 22.04.5 LTS`（`ID=ubuntu`），但 `/system/etc` **存在**（装的是 `/system/etc/firmware` 与 `/system/lib`）；而旧判据是 `Path("/etc/ohos_release").exists() or Path("/system/etc").exists()`；板上 `/etc/ohos_release` 实测不存在 | OH 特征只留 `/etc/ohos_release`；真 OH 硬件用 `MOIREGUARD_PLATFORM=ohos` 显式指定（该分支至今无硬件可验，不再做默认猜测）。修后板上 `detect_platform() -> linux` |
| `make_camera()` 吞掉显式 `device` | OHOS 分支排在函数最前面直接 `return OhosCameraAdapter()`，**根本没看 `device`**；这与它自己的 docstring（“文件/目录走 FileCamera”）相矛盾 | 改为“显式 `device` 为准，平台只挑**默认**源”；同时保留两个 fail-fast：OH 且无 device → `OhosCameraAdapter`（显式抛未实现），OH + `/dev/video*` → 依旧抛（不能假装 V4L2 能用）。修后 `--camera xxx.jpg` → `FileCamera` |

为什么只有产品路径能发现：两个都在 **factory 装配阶段**，探针脚本完全绕过了 `detect_platform` 与 `make_camera`。
已新增 `tests/smoke_board_pipeline.py` §9（6 个用例，含“开发机不给 device 仍走假源”这类反向护栏），
现 **42/42 通过**（用 `/home/plus/GpuTorch/bin/python` 跑的；上一轮因系统 python3 缺 cv2 而卡住）。

### 15.3 跨后端同图对照：转换链在**整帧**层面也等价

同一张 `samples/moire_photo.jpg`、同一套 `DetectionEngine` 滑窗与 `Decider`，只换后端与机器：

| 后端 | 命令要点 | coverage | tiles | total_ms |
|---|---|---|---|---|
| 板端 rknn fp16（NPU） | `--backend rknn --model sfrnet_wired_fp16.rknn` | **0.997290** | 32 | **96 556** |
| x86 torch fp32（RTX 5060 Ti） | `--backend torch --model SFR_best.pth` | **0.997286** | 32 | **5 381** |

⇒ **差 4×10⁻⁶**（fp16 舍入量级），逐块、融合、判定全链路一致。这是比 §14.4 单块对比强得多的证据：
它把滑窗坐标、尺寸准入填充、余弦窗融合、双门限判定一并纳入了等价范围。

同时量出真实性能：板端 96 556 / 32 = **3.02 s/块**，桌面 GPU 5 381 / 32 = **168 ms/块**（与 SDD §2 里 2026-11-05 实测的 163 ms/块吻合）——
跨后端比值 18×。§12 原先写的“单帧 2.9 s”就是把这个**单块**数当成了整帧数，已改。

### 15.4 判定层实测：干净图也报“有摩尔纹”（本轮最重的发现）

三张图走产品路径（板端 rknn 与 x86 torch 各跑，数字一致）：

| 图 | 尺寸 | tiles | threshold=0.5 下的 coverage | 判定 |
|---|---|---|---|---|
| `samples/moire_photo.jpg`（唯一真实摩尔纹正样本） | 2275×1279 | 32 | 0.997290 | True |
| `samples/clean_screenshot.png`（干净屏摄截图） | 2559×1599 | 54 | **0.999075** | True |
| `samples/clean_ide_screenshot.png`（IDE 截图） | 2559×1599 | 54 | **0.981504** | True |

⇒ **干净图的 coverage 比摩尔纹图还高**。产出的布尔量在当前配置下恒为 True。

先排除三个诱人但错误的归因（每个都查了代码，不是推论）：

| 候选归因 | 实测反驳 |
|---|---|
| 预处理口径错了 | `upstream/data_loding.py:30,53` 训练用 `PIL.convert('RGB') + ToTensor()` → **RGB [0,1]**；推理侧 `FileCamera` 做了 `BGR2RGB`、engine 做 `/255` → **一致**。上游 `predict.py:24-27` 也是 tile 512 / stride 256 / ToTensor → 我们没偏离上游推理约定 |
| tile 尺寸与训练 crop 不符 | `train.py:188` 写的是 `crop_size=512` → 与推理 tile_size **一致** |
| 转换链 / fp16 引入误差 | §15.3 跨后端 coverage 对齐到 4×10⁻⁶；且 x86 fp32 torch 上同样全 True ⇒ **与转换无关** |
| （顺带）模型输出不是概率图 | `sfrnet.py` 末端是 `nn.Conv2d(16, 1, 1)` 单通道，`train.py:42,148` 用 `BCEWithLogitsLoss` + `sigmoid(logits) > 0.5`，`metrics.py` 同口径 → **确实是摩尔纹区域分割图**，不是残差图；判定语义没走错 |

`build/_probe_threshold.py`（本轮新建，x86 torch、prob 域均匀平均同 `predict.py`）把 prob 完整分布扫了一遍：

| 图 | prob 分位数 p1/25/50/75/90/99/max | mean | thr=0.7 | thr=0.8 | thr=0.9 |
|---|---|---|---|---|---|
| 摩尔纹照片 | 0.598 / 0.835 / 0.866 / 0.882 / 0.893 / 0.908 / 0.934 | 0.848 | 0.9647 | 0.8764 | 0.0393 |
| 干净屏摄截图 | 0.599 / 0.724 / 0.742 / 0.756 / 0.774 / 0.840 / 0.950 | 0.738 | 0.8823 | 0.0408 | 0.0003 |
| IDE 截图 | 0.394 / 0.711 / 0.786 / 0.815 / 0.836 / 0.893 / 0.955 | 0.760 | 0.8193 | 0.4119 | 0.0069 |

阈值可分性（正样本 coverage − 负样本最大 coverage）：`0.5: −0.0018 ❌ / 0.7: +0.0823 ✅ / 0.8: +0.4645 ✅ / 0.9: +0.0324 ❌ / 0.95: −0.0000 ❌`

三条结论必须分开记：
1. **存在可行窗口，但起作用的是 coverage_threshold 不是 threshold**。thr=0.8 时正 0.8764 / 负上界 0.4119，
   而 `coverage_threshold=0.01` 意思是“1% 像素阳性即报警”——两张干净图都远远越线。**要分开必须把 coverage_threshold 提到 0.5 量级**。
2. **模型对一切输入都给正类**：prob 中位数 0.74–0.87，不是踩在 0.5 边上的边缘情况，而是整体偏移。这与 Q5（零内容图也全白）是同一个现象的两面。
3. **3 张样本不是标定**。两张负样本是**屏幕截图**，充满高频文本边缘，很可能不是合适的负样本定义；
   真正的负样本应该是“实拍无纹屏”，**这就是明天接摄像头才能回答的那个问题**。

没有动 `configs/default.yaml` 里的阈值：产品判定口径属于用户，且无真实数据就改默认值是把一个未标定的数换成另一个未标定的数。

### 15.5 本轮仍未闭环

- 真实负样本（实拍无纹屏）一个也没有，判定层标定（§15.4）卡在摄像头上。
- **3.02 s/块为什么这么慢**未查：折合有效吞吐仅约 0.12 TFLOP/s，远低于 RK3576 的名义 fp16 算力；
  首要候选是部分算子（OneHot / 注意力 MatMul / reflect Pad / Resize）落 CPU，需 `native_memory_size` / 逐层时间或
  分阶段子图对照实测才能定，本轮未做。
- 摄像头 V4L2 实取帧从未跑过（本轮用的是 FileCamera），明天接线后第一件事就是 `--camera /dev/video0`。
- int8 仍未转；板上的 `sfrnet_sim_fp16.rknn` / `sfrnet_wired_fp16.rknn` / 探针脚本仍在 `/root`，不入库。

## 16. 电源与连接器口径 + 关机可行性（2026-11-08 第七轮，外设未接期间的例行核对）

### 16.1 供电不是 Type-C，是 12V DC 圆口

厂商规格书 3.1 节原文（`docs/vendor/purplepi-oh2/IDO-Purple_Pi_OH2_开发板规格书.pdf`；本机没有 `pdftotext`，用 `mutool draw -F txt <pdf>` 抽取）：

| 项 | 文档值 |
|---|---|
| 主板额定电压 | **12V** |
| 电流要求 | 不小于 2A |
| 可适应范围 | 9V–26V |
| 供电接口 | J1 DC005（内径 2mm，外径 6.3mm），接电源适配器 |
| Type-C | 全板仅 J7 一个：**OTG（USB3.0 OTG 带 DP），固件下载口**，不输入电源 |
| 5V@2A | 是**对外输出**（USB3.2 Type-A 与 USB2.0 各 5V@2A；USB 外设总电流建议 ≤2000mA） |

厂商两份文档互相冲突（如实记录，不裁决）：上手指南同一处写 **J2 DC-042 / 外径 6mm**，规格书写 **J1 DC005 / 外径 6.3mm**。
两份一致的只有**内径 2mm**，选替换适配器按这一条为准，位号与外径都别信单份。

顺带排除一个候选原因：额定 12V / ≥2A（约 24 W 供给能力）远大于推理负载，所以 15.5 里那个“3.02 s/块”**不可能是供电不足**，排查不用碰电源。

### 16.2 我的一次错误归因（数据本身没错，结论却错了）

板上 `/sys/class/power_supply/` 里有 `tcpm-source-psy-2-004e`，读到 `type=USB` `online=1`。据此我曾断定“主板由 USB 供电，拔电等于拔 adb 线”。
**数据真，归因错**：那个 TCPC 观测的是 OTG 口的 VBUS（PC 侧 5V，用于 CC 与角色检测），不是主电源。同级的 `test_ac` / `test_usb` / `test_battery`
是厂商桩节点，`online` 恒为 1，拿它们判电源拓扑只会得到假结论。

纪律：**power_supply 节点存在 ≠ 该口在给系统供电**。供电口径以载板规格书为准，sysfs 只能回答“这根线在不在”。

### 16.3 关机：软件能关，而且是真断电

- PMIC 为 **RK806**（`rk806 1-0023: chip id: RK806, ver:0xa`），设备树子节点含 `shutdown-sequence` / `pmic-reset-func` / `pwrkey` / `vb-shutdown-sequence`；
  全盘**没有** `gpio-poweroff` 节点，所以走的是 PMIC 硬件关机序列，不是只 halt CPU。
- `/usr/sbin/{poweroff,reboot,halt,shutdown}` 与 `systemctl` 齐备。命令：`ssh root@192.168.31.53 "sync; reboot -p"`（adb 已移交另一台电脑，见 §16.5）。
- 异常掉电可自恢复的直接证据（就在本次开机的 dmesg 里）：
  `systemd-journald[312]: File /var/log/journal/.../system.journal corrupted or uncleanly shut down, renaming and replacing.`
  同轮无 `EXT4-fs error`、无 I/O error、rootfs 从未被 remount 成只读，`Dirty` 仅 36 kB。
  ⇒ 直接拔电的最大代价是丢一段 journald 记录，不是文件系统损坏；ext4 ordered 模式靠 journal 回放自恢复。
- **拔插 DC 圆口 = 上电即自动开机，不需按电源键**（2026-11-08 实测闭环，用户口述物理动作 + 三重日志证据）：
  1. `last -x` 里前一个 `industio :0` session 结束标记为 **`crash`**（没有 `shutdown` 记录 ⇒ 不是命令重启，是掉电）；
  2. `journalctl -b -1` 最后一条是 `12:17:01` 的 CRON / pam session closed，**没有任何 “Stopping…” 序列就断了**；
  3. 本次开机第一条是 `12:24:00 Booting Linux on physical CPU 0x0000000000`，中间无关机痕迹。
  ⇒ 顺带解开 §16.4 那条“未知重启”：它不是 reboot，是拔插断电。
- 由此 `rk806: ON: 0x40 OFF: 0x0` 有了实测锚点：在**确认是冷插电**的这次开机里读数就是 `0x40`（与上一次开机一致）。bit 含义仍未查手册，但它可以当“上电开机”的基准读数用了。
- **仍未证的另一半（不能由上面外推）**：软件 `reboot -p` 主动把 PMIC 关下去之后再上电，是否同样自动起。两条路径的 PMIC 锁存状态不同，必须单独实测：ssh 跑 `sync; reboot -p` → 等它彻底断电 → 拔插 DC → 看起不起。
- 附带发现：`last -x` 显示本次进入 `runlevel (to lvl 5)` 且 `industio :0  still logged in`，dmesg 里有 `input: rk805 pwrkey ... input0` ⇒ **出厂镜像带 GNOME 桌面 + 自动登录**（也解释了 §15 里 gnome-shell 的常驻 CPU 占用）。交付形态要关掉桌面与自动登录，属 §10.3 那类安全项。

### 16.4 通道现状快照（会过期，别当长期结论）

同一天里通道状态变过两次。两个时刻都记，因为“取一次就当结论”正是本轮出错的方式：

| 通道 | 12:13（第一次核） | 13:05（板子重启后） |
|---|---|---|
| USB adb | `98fed70982ab4c63 device` 正常 | **本机看不到设备**（原因见 §16.5：USB 已移交另一台电脑） |
| 网络 22 端口 | **不通**（ping 无回音，22 / 5555 连不上） | **通**，`sshd` 在 `0.0.0.0:22` 与 `[::]:22` |
| 板侧 wlan0 | UP、`192.168.31.53/24`、`Xiaomi_432` ch11、rssi −42 | 同一地址，仍 UP |
| 开发机侧 | `eth4 192.168.31.6/24`（WSL mirrored），`ip route get` 出接口正常 | 同 |

板端 `uptime` 从 `1 day 14:27` 变成 `41 min` ⇒ **中间发生过一次重启**。于是两条旧结论同时作废：

1. 我写的“断点因此落在中间那一段（AP 客户端隔离 / Windows 防火墙 / vSwitch 三选一）”**不成立**——重启就通，说明问题就在那一次运行状态里（板侧），而我只看了 `wlan0 UP` + 有 IP + 有路由就下了“板侧正常”。
2. 下次再碰到 ping 不通，先查板侧 `iw dev wlan0 link` / ARP / 重连，而不是先怀疑防火墙。`wlan0 state UP` **不等于链路可用**（ dhd fullmac 在 rssi −42 的情况下仍可能不对高层交付）。

### 16.5 adb 消失的真实原因：USB 已接到另一台电脑（同一件事我错连两层）

13:05 走 ssh 回查板端：

| 证据 | 读数 | 当时错在哪里 |
|---|---|---|
| `pgrep -a adbd` | `1425 /usr/bin/adbd` | 这条是对的：设备端 daemon 活着 |
| `/sys/kernel/config/usb_gadget/` | `rockchip` | 这条是对的：gadget 已注册 |
| `cat /sys/class/udc/*/state` | **`configured`** | **读数对、推论错**：它只说明“有一台主机已枚举并配置了它”，**不说明是哪一台** |
| `systemctl is-enabled adbd` | 无 unit，`inactive` | 这条是对的 |

由此得到的三层猜测均已被推翻：① “检查中途从枚举消失，属物理动作所致（拔线）”→ 错；② “断点在 Windows 侧 USB 驱动 / 接口绑定”→ 也错（虽然 `DriverAssitant_v5.13` 确实是常见原因，但这次不是）；
**真实原因是用户把板子的 USB 线接到了另一台电脑上**，所以那台主机看到的是 `configured`，本机 `adb devices` 当然为空。这是本轮唯一一条只能由用户提供的信息——接的是哪台主机，板侧遥测看不出来。

修正后的判据：`udc state=configured` + 本机 `adb devices` 为空 ⇒ **先问一句“线插在哪台机器上”**，确认就在本机之后再去查驱动绑定（DriverAssitant）；
`state` 缺失或 `not-bound` 才该怀疑线与口。

**操作后果（明天直接受影响）**：本机已经没有 adb 通道，唯一可用的是 ssh/scp。需要进 Loader/Maskrom 刷机的话，得到**接管 USB 的那台电脑**上做（§5 的烧录序列本来就是 Windows 侧操作，现在多一层：先确认线在哪）。

### 16.6 主通道改成 SSH（本轮最有用的产出）

板端 `PermitRootLogin yes`，root 口令登录可用；已将开发机公钥装入板上 `root:~/.ssh/authorized_keys`，之后一律免密：

```bash
ssh -o BatchMode=yes root@192.168.31.53 'id -un; uptime -p'   # 实测 KEY_LOGIN_OK，不回退到密码
scp build/xxx root@192.168.31.53:/root/                       # 双向往返内容一致已实测
```

重置的那个口令 **不入库、不入文档、不入记忆**，只用于首登与装公钥；后续一律走密钥。想收回去只需删掉板上 `authorized_keys` 里那一行并改密码。

沙箱的坑（下次还会咬）：WSL 里 `ssh`/`scp` 默认要碰 `~/.ssh` 与 `/tmp`，而 IDE 沙箱把 `~/.ssh` 挂成空 tmpfs、`/tmp` 只读，
于是报 `Identity file ... not accessible` 与 `Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf`。
对策：known_hosts 指到工作区内（`-o UserKnownHostsFile=build/.kh -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=no`，用完删），
或在沙箱外执行；首次密码登录用 paramiko（GpuTorch 环境里有）最省事，它不读那些 ssh_config。

## 17. OpenHarmony 侧 bring-up 三条硬结论，与工程主线退回 Ubuntu（2026-11-08～11-09 第九轮）

一句话：新板烧 KaihongOS 5.0.1（API 14，kernel 6.1.75 #37）后链路能通、真权重能跑，但**系统每约一小时被强制重启一次**，且板端解除不了；据此把工程主线退回 Ubuntu 22.04（§17.5）。

### 17.1 每小时重启：三样本 + 一次提前 40 分钟的预测命中

取证做法（`build/watch_oh_reboot.sh`）：每 30 秒读一次 `/proc/uptime`，与上一读比对，回零即重启点，回零处那个秒数就是周期。

| 第几程 | 开机时刻 | 观测到的 uptime 终点 | 判定 |
|---|---|---|---|
| 首启 | 22:03:08 | 229.68 s | 烧录后的一次性重启 |
| 二程 | 22:07:09 | 3615.16 s | **一小时** |
| 三程 | 15:53:53 | 3617 s | 复现 |

三程这条是本轮补的，也是最有分量的一条：在 uptime=1360 s 时按「开机 +3615 s」预测下次重启约 16:54，随后实测本次开机时刻为 16:54:10，**命中**。

由此钉死两条判据：

- 周期按 **uptime** 计（3615 / 3617 两样本），不是墙钟 ⇒ **校时救不了**。板内 RTC 慢约六个月（`Fri Mar 27 16:57:12 CST 2026`，真实为 11 月）是另一条独立缺陷，任何按墙钟排程的逻辑上板前必须先校时。
- 首启那 229 s 与后面的整点周期不是一回事。我此前说过「更像烧录后首启的一次性重启，不是定时器」，**这句是错的**，被二程的 3615.16 s 直接否证；当时 session 2 已活过 1437 s 就该反过来怀疑周期性，而不是急着给「一次性」定性。

根因是**系统授权未激活**，证据链齐：`licenseMonitor`(318) / `license_sa`(1037) / `khdeviceauth_service`(319) 在跑；hilog 报 `[factory.cpp(200)] LicenseData size check failed` 与 `[license.cpp(417)] Read license data from file fail`；SystemUI 订阅 `usual.event.DEV_NOT_ACTIVATED`；`/system/etc/license/` 里是一整套按分辨率准备的未激活水印 PNG（因不接显示而 `createWaterMask fail for screen width or height invalid`，水印才没露出来）。激活需厂商三要素 `OPR90338461` / `HPR47075475` / `CUS44050207` 加 SN `2c45fe421ba5701d`。

**不做的事及其理由**：不停用授权服务来「治」重启——`khlicense_monitor.cfg` 写的是 `"critical":[1,1,10]`，critical 服务异常会连累整机重启，杀掉它比每小时一次更糟；且这属于授权绕过，不在工程范围内。正路只有拿 SN 与三要素找厂商开通。

### 17.2 WiFi：芯片、驱动、固件三样都在镜像里，缺的只是有人把它们接上

先钉身份：SDIO 卡 `0x02d0:0xa9bf`（Broadcom），驱动读 F1 signature `0x15294345` ⇒ `chip:0x4345 rev:0x9 pkg:0x2`，即 **BCM43456C5，模组 AP6256**——不是规格书与固件名容易让人以为的 AP6275S。

**缺陷 A：驱动 `.ko` 躺在盘上，开机没人加载。** `/chip_prod/modules/bcmdhd_sdio.ko`（2,761,480 B）是全镜像唯一的 `.ko`；而 `lsmod` 只有表头、`/sys/class/ieee80211` 为空、三个 SDIO 功能的 `driver` 链接全部不存在。在 `/system/etc/init`、`/vendor/etc/init`、`/sys_prod/etc/init`、`/chip_prod/etc/init` 四处搜 `insmod|modprobe|bcmdhd`，**零命中**。

**缺陷 B：`/vendor/etc/firmware/` 放的是另一颗芯片的固件。** 驱动按芯片名去搜 `fw_bcm43456c5_ag.bin` / `nvram_ap6256.txt`，那里却只有 `fw_bcm43752a2_ag.bin` + `nvram_ap6275s.txt`（43752 / AP6275S 的）。驱动要的这套实际在 **`/chip_prod/etc/firmware/`**：`fw_bcm43456c5_ag.bin` 562,387 B、`nvram_ap6256.txt` 2,212 B、`config_bcm43456c5_ag.txt` 18 B，时间戳 `2025-12-05 16:42` 比同目录其余 `2025-10-22` 的文件新——像是后补过但补错了分区。`clm_bcm43456c5_ag.blob` 两个目录都没有，与 §11.2 属同一族问题，Linux 侧缺 CLM 的处置口径可参照。

实测启用（两条命令，可逆，不落 flash）：

```bash
insmod /chip_prod/modules/bcmdhd_sdio.ko \
  firmware_path=/chip_prod/etc/firmware/fw_bcm43456c5_ag.bin \
  nvram_path=/chip_prod/etc/firmware/nvram_ap6256.txt
/vendor/bin/ip link set wlan0 up        # 必须用 /vendor/bin 那份 ip，见 §17.3
```

```
Time taken for FW download and F2 ready is: 124 msec
[wlan0] dhd_open : Exit ret=0
Register interface [wlan0]  MAC: 10:a5:62:1a:12:ea
dhd_conf_set_country : Country code: CN (CN/38)
```

关联层是现成的：`/vendor/bin/wpa_supplicant` 是 **v2.10 且 `drivers:` 里有 nl80211**（内核 `CONFIG_CFG80211_WEXT is not set`，所以只能走 nl80211）。起起来后真实扫出 20+ 个热点，`Xiaomi_432` 在 2.4G ch11 与 5G ch36 上分别为 −53 / −49 dBm，说明天线已接到位。

**没做成的一步要说清**：关联需要热点口令，本轮没拿到，因此**从未发生过一次真实连接**；「OH 侧 WiFi 能上网」目前只到接口级验证。另外镜像里确实没有任何命令行 DHCP 客户端（`toybox --list` 无 `udhcpc`，全盘 `find -name "*dhcp*"` 只有 `libdhcp_*.z.so`），IP 得靠 `netmanager`(596) 这个 SA 来发，兜底才用静态地址。

### 17.3 本轮在 WiFi 上说错的三句（同一类错，集中记）

| 说过什么 | 真相 | 错在哪一层 |
|---|---|---|
| 「板上没有 wpa_supplicant / udhcpc」 | `/vendor/bin/` 里 `wpa_supplicant`、`hostapd`、`wpa_cli`、完整版 `ip` 全都有 | 判据用了 `which`，而 PATH 不含 `/vendor/bin`。「找不到」必须是搜索域的结论，不能是 PATH 的结论 |
| 「SELinux 拦住了 `ip link set`」 | 那条 `Operation not permitted` 是驱动 `dhd_open` 失败回上来的；换对固件路径后 rc=0 | 把上层报错当权限拒绝，且没去读同一时刻的 dmesg |
| 「探测文件里已有完整 dmesg」 | `dmesg` 接 `tail` 的那条管道写在引号外，`tail` 被 Windows 抢走，dmesg 整段丢失 | 跨 WSL/PowerShell 传参时管道必须整体交给板内 shell；本轮脚本已改成 bash 直调 `hdc.exe` |

同类旧错一并留档：我曾从 `ls /system/bin` 的文本里正则筛出 `dropbear` 字样就断言其存在，实际不存在——同一个筛法还害我误判过 shadow 文件与 `udhcpc`。

### 17.4 SSH 从零跑通，且本轮验证了它能扛过重启

§16.6 那套 SSH 是 Ubuntu 时代的；OH 侧此前完全没有 SSH，本轮从零建成，并且**赶上一场真实重启做了免费验证**：重启后 `sshd` 由 init 自启（PID 354，STIME `15:53:53`），`0.0.0.0:22` 在听，`/system/etc/init/sshd.cfg` 逐字读回一致，`/etc/passwd` 仍是 204 行且 root 行保留 `$6$…` 哈希。

这修正了一个默认假设：**模块加载是易失的，落盘的改动不是**。`hdc target mount` 把 `/system` 转可写后写进去的东西进了 flash，重启仍在。

配方里每一条都有实调依据，摘最容易被重试踩的：

- `sshd.cfg` **刻意不写 `secon`、不标 `critical`**：OH 私有 SELinux 标签（如 `u:r:licenseMonitor:s0`）写错 init 会直接拒起；标 critical 则服务失败连累整机重启。
- 启动必须给绝对路径 `/system/bin/sshd`，否则 `sshd re-exec requires execution with an absolute path`；且必须显式 `-f /system/etc/ssh/sshd_config`——该 build 的默认配置路径不是 `/etc/ssh/sshd_config`，`sshd -t` 不带 `-f` 会假报 `no hostkeys available`。
- host key 复用厂商留下的 `/system/etc/ssh/id_rsa`（不叫 `ssh_host_*`，故必须显式 `HostKey`），权限收到 0600。
- **`UsePAM no` 是必需的**：`/etc/pam.d/sshd` 里 `@include common-auth` 被注释掉，认证栈为空。而镜像**没有任何 shadow 文件**，口令哈希只能放在 `passwd` 第 2 字段；同时第 7 字段（登录 shell）原本全是 `/bin/false`，**两处必须一起改**，只改其一就是「设了密码也进不去」的半死状态。改哈希用 `openssl passwd -6`（`passwd`/`chpasswd` 在这镜像里不可用：前者要 tty，后者二进制不存在）。
- root 的 home 字段原本为空 ⇒ 登录报 `Could not chdir to home directory`，设为 `/data/root`(0700) 解决。
- 端到端登录实测走 `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force`（本机无 `sshpass`），经 `hdc fport tcp:2222 tcp:22` 返回 `uid=0(root) context=u:r:su:s0`。
- 原文件备份留在 `/data/passwd.bak.20261108`（7,499 B）作回退点。

附带安全发现（如实记，不写值）：`param get` 里存在明文的 `khdfx.remote.username` / `khdfx.remote.passwd`。这属于厂商固件的凭据暴露面，若项目走 OH 交付需向厂商提出。

### 17.5 系统选型：为什么退回 Ubuntu，代价与可逆性

先对齐项目自己的旧账：`OpenHarmony 实施策略` 早已定为「仅为架构预留，实际开发不建议投入；Python 应用无法直接进 OHOS 镜像，需走独立进程 + IPC」；而 §13.1 那条版本链结论（转换端 2.3.2 ↔ 板端 `librknnrt.so` 2.1.0）**本来就是在 Ubuntu 上实测得到的**。所以退回 Ubuntu 不是推翻决策，是回到既定主线。

| 判据 | OH（本轮现状） | Ubuntu（回退目标） |
|---|---|---|
| 授权门 | 未激活 ⇒ 每 3615 s 强制重启 | 无此机制 |
| 无人值守定位 | **正面冲突**：推理进程、MQTT 会话、滑窗状态、日志缓冲每小时全断 | 满足 |
| WiFi | 芯片/驱动/固件全在，但需手补两个缺陷才能起接口 | 手册原生 `nmcli` / NetworkManager 路径 |
| RKNN 链路 | fp16 .rknn 已跑通（rel 0.0059 / corr 1.0000） | §13.1 结论原生适用，模型侧零重做 |
| Python 应用 | 需独立进程 + IPC | apt + cp310 wheel 口径对齐 |
| 工具链 | 无命令行 DHCP、无 shadow、PAM 被剔空 | systemd 常规生态 |

代价与可逆性：

- 重烧会**擦掉整盘含 userdata**。OH 上 `/data`（mmcblk0p12）53 G 仅用 240 M，内容主要是日志，用户已确认不撤。
- OH 侧本轮成果（§17.2 两条命令、§17.4 整套 sshd 配方）会随镜像消失——所以必须先落到本节。
- **可逆**：`/mnt/d/rkflash/OH_251205_HDMI4K` 与两个 OH `.img` 及 MD5 均在本机，随时能烧回去。
- Ubuntu 那颗是打包 `update.img`：头 4 字节 `RKFW`，LBA1 含 `RKNS` / `FlashBoot`，内含 parameter / uboot / system / userdata；走 RKDevTool「升级固件」整包覆盖，比 §17 之前那次逐分区填 10 行简单。烧录源文件完整性锚点：**6,163,532,419 B**，`md5 = 8cdd8d825ea8976239a702b7586f3406`（Windows 侧用 `certutil -hashfile <路径> MD5` 对一眼再开烧）。
- 烧录前提未变：只能在 Windows 侧做（WSL 不透传 USB 烧录设备），且一旦中途失败，必须**拔 12V ≥ 10 s + 按住 Recovery 上电**才能复位僵死的 mini-loader。
- 仍要保留的说法：OH 侧不是「失败」而是「已验证到接口级并定位到两个厂商缺陷」。未推送的验证产物（真权重上板、整帧跨后端对照、两个装配缺陷修复与 42/42 冒烟）不因换系统而作废，它们是回答「能不能上鸿蒙」的实测依据。


