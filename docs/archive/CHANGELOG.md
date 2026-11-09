# 变更日志 (Changelog)

本文件记录项目的所有变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [Unreleased] - 2026-11-09（第九轮：OH 侧 bring-up 收口——查清每小时重启的真实机理，工程主线退回 Ubuntu）

新板烧 KaihongOS 5.0.1 后一路做到 SSH 与 WiFi 可用，但本轮最重的产出是一条**否决性事实**：系统每约一小时被强制重启一次，且板端解除不了。据此定系统选型，详情全部落在 [BOARD_BRINGUP §17](../guides/BOARD_BRINGUP.md)。

### Verified（全部可在板上复核）
- **每小时重启拿到三样本**：`build/watch_oh_reboot.sh` 每 30 s 采一次 `/proc/uptime`，周期分别为 229.68 / 3615.16 / 3617 s。并且做了独立预言：在 uptime=1360 s 时按「开机 +3615 s」预测下次重启约 16:54，实测本次开机 16:54:10——**命中**。周期按 uptime 计而非墙钟，所以校时不能解除。
- 根因定为**授权未激活**：`licenseMonitor`(318) / `license_sa`(1037) / `khdeviceauth_service`(319) 在跑，hilog 报 `Read license data from file fail` 与 `usual.event.DEV_NOT_ACTIVATED`，激活需厂商三要素 OPR/HPR/CUS 加 SN。
- WiFi 芯片身份闭环：SDIO `0x02d0:0xa9bf`，驱动读 F1 signature `0x15294345` ⇒ `chip:0x4345 rev:0x9 pkg:0x2` = **BCM43456C5（模组 AP6256）**，不是固件名暗示的 AP6275S。
- **两条命令把 wlan0 拉起来**：`insmod` 带正确固件路径 + `/vendor/bin/ip link set wlan0 up` ⇒ `FW download and F2 ready is: 124 msec`、`dhd_open : Exit ret=0`；`wpa_supplicant` v2.10(nl80211) 起后真实扫出 20+ 热点，`Xiaomi_432` 达 −49 dBm（天线到位）。
- **SSH 扛过一场真实重启**：`sshd` 由 init 自启（PID 354，STIME `15:53:53`），`0.0.0.0:22` 在听，`/etc/passwd` 仍 204 行且 root 哈希保留。由此修正一个默认假设：**落盘的改动不易失，模块加载才易失**。

### Found（两个厂商缺陷，都不是硬件问题）
- **缺陷 A：开机不加载驱动。** `/chip_prod/modules/bcmdhd_sdio.ko`（2,761,480 B）是全镜像唯一的 `.ko`，但四处 init 目录里搜 `insmod|modprobe|bcmdhd` **零命中** ⇒ `lsmod` 空、`/sys/class/ieee80211` 空、三个 SDIO 功能全无 driver 绑定。
- **缺陷 B：固件放错分区。** 驱动按芯片名在 `/vendor/etc/firmware/` 找 `fw_bcm43456c5_ag.bin` / `nvram_ap6256.txt`，那里只有 43752/AP6275S 的文件；驱动要的那套实际在 **`/chip_prod/etc/firmware/`**（且时间戳 `2025-12-05` 比同目录其余 `2025-10-22` 新，像是后补过但补错了地方）。`clm_bcm43456c5_ag.blob` 两边都没。
- 另查清：镜像里没有任何命令行 DHCP 客户端（`toybox --list` 无 `udhcpc`，全盘只有 `libdhcp_*.z.so`），IP 得靠 `netmanager` SA。

### Corrected（本轮我自己说错的四处，同类错集中到一处记）
- 「更像烧录后首启的一次性重启，不是定时器」——被 3615.16 s 直接否证。当时 session 2 已活过 1437 s，就该反向怀疑周期性，而不是急着定性。
- 「板上没有 wpa_supplicant / udhcpc」——全在 `/vendor/bin/`。错因：判据用了 `which`，而 PATH 不含 `/vendor/bin`。**「找不到」得是搜索域的结论，不能是 PATH 的结论**。
- 「SELinux 拦住了 `ip link set`」——那条 `Operation not permitted` 是驱动 `dhd_open` 失败回上来的，换对固件路径后 rc=0。把上层报错当权限拒绝，且没读同一时刻的 dmesg。
- 「探测文件里已有完整 dmesg」——`dmesg | tail` 的管道写在引号外，`tail` 被 Windows 抢走，dmesg 整段丢失。本轮脚本改为 bash 直调 `hdc.exe`，管道整体交给板内 sh。

### Decided
- **工程主线退回 Ubuntu 22.04**（`IDO_PurplePiOH2_V1B_Ubuntu22.04_HDMI4K_250907R.img`，2026-11-06 已在本板实测烧录成功；打包 `update.img`，头 4 字节 `RKFW`，走 RKDevTool「升级固件」整包覆盖）。理由不是「鸿蒙不好」，而是每小时重启与「板端无人值守常驻服务」正面冲突；且项目旧决策本就把 OH 定为架构预留、不建议实际投入，§13.1 的版本链结论本来就在 Ubuntu 上得到。
- **不做授权绕过**：`khlicense_monitor.cfg` 标了 `"critical":[1,1,10]`，杀掉它会连累整机立刻重启，比现状更糟；正路只有拿 SN 与 OPR/HPR/CUS 找厂商开通。
- OH 镜像（`OH_251205_HDMI4K` 及两个 `.img` + MD5）均在本机保留，**选型可逆**。
- **文档口径已跟改**：README / SDD / build/README 原先把 Linux 与鸿蒙当对等交付目标（“同时支持双系统”“双系统各构建一条流水线”），已全部改为“工程主线 Ubuntu 22.04，OH 仅架构预留”；四层架构与适配层抽象不动，“同一套代码”这个约束保留且新增一条实测支撑（风险 #13 的平台误判只改适配层即恢复）。SDD §6 新增风险 #14（授权门）并把 #3 / #4 降级为预留口径。`python3 build/check_docs.py` rc=0。

### Known Issues
- OH 侧 WiFi **本轮从未真实关联**（缺热点口令），只到接口级验证；不要把「能扫到」读成「能上网」。
- 板内 RTC 慢约六个月（`Mar 27 2026` vs 真实 11 月），独立缺陷未修；任何按墙钟排程的逻辑上板前必须先校时。
- `param get` 里存在明文的 `khdfx.remote.username` / `khdfx.remote.passwd`，属厂商固件凭据暴露面（值不入文档）。
- `/mnt/d/rkflash/` 下含 root 口令哈希的中间文件（`passwd.cur` / `passwd.new` / `root_hash.txt`）仍在盘上未清（本轮用户未选清理）。

### 元教训
1. **周期性现象至少要覆盖两个周期再下结论**。单样本（229 s）促成的「一次性重启」判断错了整整一天，而它直接决定了系统选型。
2. 板子里「找不到 X」有三层可能：PATH 不含它、搜索域不覆盖它、文本筛法骗人。先 `find / -name` 再下结论。OH 把可执行文件分散在 `/system/bin` 与 `/vendor/bin`，PATH 只给前者。
3. 上层工具报 `Operation not permitted` 不等于权限拒绝；驱动类接口一定要与同一时刻的 dmesg 对时再推因。
4. 跨 WSL/PowerShell 传参时，管道与引号的归属是首要风险：要么整体单引号交给板内 sh，要么取回本机再筛。

---

## [Unreleased] - 2026-11-08（第八轮：测试图集整理与编号——顺便查出一个滑窗小图缺陷）

用户交入 `测试图片/`（494 张 / 2.1 GB）。先按请求写忽略规则，再整理编号；编号本身很轻，重的是审计翻出来的三件事。

### Added
- **`.gitignore` 新增 `测试图片/`**（沿用 `samples/`、`captures/` 先例：平目录忽略，不留 .gitkeep）。数据不入库，但入库两样东西：
  `docs/dataset/test_images_manifest.csv`（494 行 / 80 K，含旧名、新名、拍摄时间、宽高、字节、md5）与 `docs/dataset/README.md`。
  这样处理是因为：2.1 GB 不能进 git，但**没有清单的结论就是无源断言**，换机器即丢。
- **`build/organize_images.py`**：`plan` / `apply` / `verify` / `revert` 四个动作。改名前先落清单、apply 前全量预检目标冲突、
  verify 逐张重算 md5。只改名不改字节，凭清单可完整回滚。
- 编号方案：`moire_0001..0291`（手机直出，按文件名时间戳）、`moire_0292..0301`（原编号系列）、`clean_0001..0193`。
  **前缀即真值标签**，`glob("moire_*")` / `glob("clean_*")` 就能拿到带标签集合，不再依赖目录名。

### Verified
- 494 张**全部可解码**，无 0 字节、无内容重复（md5 分组 0 组）；`apply` 改名 494/494，`verify` 缺失 0 / md5 不符 0 / 残留 0。
- 142 种分辨率；`宽 ≥3000` 的土规则（不看摩尔纹）精度 **100.0%**、对正样本召回 **96.7%** ⇒ 尺寸与标签近乎共线。
- 按 `tile_size 512 / stride 256` 实算块数跨 **0–320**：`moire` 291 张恒 165 块，`clean` 多为 4 或 8 块；
  对照值 `2275×1279 → 32 块` 与板端产品路径那次数值一致，公式可信。
- **`SlidingWindowEngine.plan()` 小图越界（实跑复现）**：任一边 ≤512 而另一边 >512 时 `rows` 为空，`sliding_window.py:112` 的
  `rows[-1]` 抛 `IndexError`（本批 15 张命中，多为 1280×4xx 窄窗口截图）；兜底分支 `sliding_window.py:100` 要求两边都 ≤512。

### Corrected
- 我上一轮报“负样本 193 张”时只数了磁盘现存，没核编号段 ⇒ 口径不完整：原批 **300**，缺 **107**，且散布在 0001–0300 每一段（不是尾部截断）。
- 我一次统计输出“以 100% 精度识别 294/301”是自算错：那个档位混进 3 张 1280×3173/3775/**20835** 的滚动长截图。
  改用宽度口径才是 291/291 = 100% 精度、96.7% 召回。数字来路没核对就念出来，是同一类错。
- awk 里用 `"0001"` 当数组键、拿数字 `1` 去查，前导零让六段全报“有 0/50”（明明 193 张）。已改 `$1+0` 重跑。

### Known Issues
- **小图 `plan()` 越界未修**：`engine.py:248–252` 会包装成 `MoireEngineError("capture", ...)`，故推断为“该帧失败、循环不死”，
  但该路径对外的 HTTP 状态码与日志表现**本轮未实跑验证**。一张普通窗口截图即可触发，无人值守设备属必修项。
- **107 张缺失原因未澄清** ⇒ 若删除标准与“看起来有没有纹”相关，负样本会被系统性拉向容易判对，**本批不足以对外宣称误报率**。
- 手机 4K 直出的摩尔纹空间周期 ≠ 板端 IMX415 采到的周期，归一化只保证内部可比；**明天接上摄像头后必须用实拍帧重标一次**。

### 元教训
1. **标定前先做几何审计**：块数分布、尺寸×标签交叉表、零块图，这三项任何一项没过，混池算出的指标都可能只是分辨率在起作用。
2. `mtime` 是拷贝时间不是拍摄时间；顺序信息只在文件名里（`IMG_YYYYMMDD_HHMMSS`）。
3. 大数据不入库时，**清单必须入库**：否则“数据集版本”这件事无法被任何后来者复核。

---

## [Unreleased] - 2026-11-08（第七轮：电源口径纠正——我把 TCPC 的 VBUS 误读成了主电源）

外设未接期间的一次例行核对：查通道、查关机能力、查供电规格。结论落 `docs/guides/BOARD_BRINGUP.md` 新增的 §16。

### Verified
- **USB adb 通道在**：`98fed70982ab4c63 device`，走 `Tools/ADB工具/adb_win/adb.exe`（WSL2 interop；C 盘未挂载但工作区内的 exe 能跑）。板端 `uptime 1 day 14:27`、rootfs `/dev/mmcblk0p8 ext4`（eMMC）、`/sys/block/` 无 mmcblk1 ⇒ **SD 卡仍未插**；`pgrep` 无残留推理进程，load ~1.0 全来自系统服务。
- **软件关机是真断电**：PMIC **RK806** 设备树带 `shutdown-sequence`，全盘无 `gpio-poweroff` ⇒ 走硬件关机序列而非 halt CPU；`sync; reboot -p` 可用。
- **主通道从 adb 换成 SSH（本轮最有用的产出）**：用户重置口令后首登成功，已把开发机公钥装入板上 `root:~/.ssh/authorized_keys`，之后 `ssh -o BatchMode=yes root@192.168.31.53` 实测 `KEY_LOGIN_OK` 且不回退密码；scp 双向往返内容一致。板端 `PermitRootLogin yes`、`sshd` 监听 `0.0.0.0:22` 与 `[::]:22`、`udc state=configured`。重置的那个口令不入库不入文档不入记忆。
- **上电即自动开机，不用按电源键**（用户口述物理动作是**拔插 DC 圆口**，再加三重日志证据）：`last -x` 里前一个 `industio :0` session 结束标为 **`crash`**（无 `shutdown` 记录）；`journalctl -b -1` 最后一条在 `12:17:01` 且**没有任何 “Stopping…” 序列**；本次开机首条 `12:24:00 Booting Linux on physical CPU`。⇒ §16.4 里那条“未知重启”就是这次拔插，不是有人执行了 reboot。附带：`ON: 0x40` 现在可以当“上电开机”的基准读数（bit 含义仍未查手册）。
- **异常掉电自恢复已有直接证据**：本次开机 dmesg 里就写着 `systemd-journal ... corrupted or uncleanly shut down, renaming and replacing`，且无 `EXT4-fs error` / 无 I/O error / rootfs 从未被踢只读，`Dirty` 仅 36 kB ⇒ 直接拔电的最大代价是一段 journald 记录，不是文件系统。
- **供电规格（厂商规格书 3.1 节）**：额定 **12V**、电流不小于 2A、适应 9V–26V，经 **J1 DC005 圆口**（内径 2mm）接适配器；全板**只有一个 Type-C，是 J7 OTG（固件下载口），不输入电源**；文档里的 5V@2A 是对外给 USB 外设的输出。

### Corrected
- **推翻本会话上一步我自己的结论**：上一轮我说“供电是 Type-C，物理断电等于拔 adb 线”。`tcpm-source-psy-2-004e` 的 `type=USB online=1` 是真数据，但那个 TCPC 看着的是 **OTG 口的 VBUS**（CC/角色检测用），不是主电源；`test_ac`/`test_usb`/`test_battery` 是厂商桩节点，`online` 恒 1。纪律：**power_supply 节点存在 ≠ 该口在给系统供电**，供电口径以载板规格书为准。
- 附带排除一个候选归因：额定 12V/≥2A（约 24 W）远大于推理负载 ⇒ “3.02 s/块”不可能是供电不足，排查不用碰电源。
- 厂商文档自身冲突已记录不裁决：上手指南写 J2 DC-042/外径 6mm，规格书写 J1 DC005/外径 6.3mm，只有**内径 2mm** 两份一致。
- **推翻本轮自己写的通道结论（同一轮内连错三层，详见 §16.4 / §16.5）**：①“网络断点落在中间那一段（AP 隔离 / 防火墙 / vSwitch）”——板子重启后直接通，问题就在那一次运行状态里；我当时只看了 `wlan0 UP` + 有 IP + 有路由就判“板侧正常”，**`state UP` 不等于链路可用**。②“adb 从枚举消失属物理动作所致（拔线）”——板端 `adbd` 在跑、gadget 已注册、`udc state=configured`，线确实没拔。③ 改口成“断点在 Windows 侧 USB 驱动 / 接口绑定”——**还是错**。最终答案只能由用户给出：**USB 线已接到另一台电脑上**。`configured` 只证明“有一台主机配置了它”，不证明是哪一台。

### Known Issues
- **本机已无 adb 通道（不是故障，是物理拓扑变了）**：用户把板子 USB 接到了另一台电脑，所以本机 `adb devices` 永远为空而板端一切正常。**现在唯一可用的是 ssh/scp**；要进 Loader/Maskrom 刷机必须到接管 USB 的那台电脑上做（§5 烧录序列多一层前置：先确认线在哪）。
- **仍未证：`reboot -p` 之后重新上电是否同样自动起**——PMIC 锁存路径与冷插电不同，不能外推。实测法：ssh 跑 `sync; reboot -p` → 确认彻底断电 → 拔插 DC → 看起不起。
- **出厂镜像带 GNOME 桌面 + 自动登录**（`runlevel to lvl 5`、`industio :0 still logged in`、`input: rk805 pwrkey`）：既解释了 gnome-shell 的常驻 CPU 占用，也是交付前要处理的安全项（归 §10.3）。

### 元教训
1. **拿到真数据不等于拿到真结论**：本轮错在把“检测到 VBUS”读成“由此供电”，而两份厂商 PDF 就在仓库里没翻。涉硬件规格先抽文档，sysfs 只做交叉验证。
2. `docs/vendor/` 里的 PDF 用 `mutool draw -F txt <pdf>` 可直接抽文字（本机无 `pdftotext`），不必靠印象或网上查规格。
3. **遥测有它的天花板：板侧看不出来“线插在哪台主机上”。** 本轮 `configured` 被我连读两次错（拔线→驱动），真因只能由用户提供。涉及物理拓扑的归因，**先把“接线现状”问完再下结论**，别拿一个中性读数去反推故障。

---

## [Unreleased] - 2026-11-07（第六轮：产品代码路径上板——链在整帧层面也等价，但两个装配缺陷只有产品路径能发现）

承上一轮：转换链已用我自己的探针跑对，但 `app.main → DetectionEngine → RknnRuntime` 这条**真实路径**从未在板上跑过。本轮把它推上板跑了一次，一个命令就炸。

### Verified
- **产品路径上板全绿**：`python3 -m app.main --backend rknn --model sfrnet_wired_fp16.rknn --camera moire_photo.jpg --frames 2` → `EXIT=0`，`BOOTING 完成 READY 耗时 4106ms`，两帧 `has_moire=1 coverage=0.997290 tiles=32 total_ms=96556 / 96615`。`RknnRuntime` 的加载/预热/NHWC 入/NCHW 出修正均已在产品代码上实跑验证（不只是探针）。
- **跨后端同图对照（本轮最强证据）**：同一张图、同一套滑窗与判定，只换后端与机器——板端 rknn fp16 coverage **0.997290** vs x86 torch fp32 **0.997286**，差 4×10⁻⁶。等价范围从“运行时边界”扩到了滑窗坐标、尺寸准入填充、余弦窗融合、双门限判定全链路。
- **延迟量纲修正（上一轮自己写错了）**：§14.4 的 2.83–3.15 s 是**单块 512×512**，我一直当“单帧”写。整帧 2275×1279（32 块）实测 **96.6 s**（板端）/ 5.38 s（桌面 GPU，168 ms/块，与 SDD §2 里 2026-11-05 的 163 ms/块吻合）⇒ 跨后端 18×，4K 外推约 340 s。SDD §2 那段“4K 13–22 s”的算术推算也被实测证伪（差 15–25 倍），错在拿 int8 峰值去除 fp16 的活 + 直接借“30–50% 利用率”。
- **判定层实测（本轮最重的发现）**：干净屏摄截图也 `has_moire=True`，coverage **0.9991 / 0.9815**，比摩尔纹图 0.9973 还高。已用代码证伪四个候选归因：预处理口径（训练与推理都是 RGB [0,1]，上游 `predict.py` 可证）、tile 尺寸与训练 crop 不符（`train.py:188` 就是 512）、转换链误差（跨后端对齐 4e-6）、输出不是概率图（`BCEWithLogitsLoss` + `sigmoid>0.5`，确实是分割图）。`build/_probe_threshold.py` 扫描：thr=0.8 时正 0.8764 / 负上界 0.4119——**存在可行窗口，但起作用的是 coverage_threshold（需从 0.01 提到 0.5 量级）而不是 threshold**。**未改 `configs/default.yaml`**：产品判定口径属用户，且 3 张样本（两张还是屏摄截图）不构成标定。
- **冒烟测试换了正确的解释器重跑**：用 `/home/plus/GpuTorch/bin/python` 跑 `tests/smoke_board_pipeline.py` → **42/42 通过**（上一轮用系统 python3，缺 cv2 根本没跑成）。

### Fixed
- **`detect_platform()` 把真板子判成鸿蒙**：旧判据含 `Path("/system/etc").exists()`，而 RK3576 厂商 Ubuntu 22.04 固件本身就带 `/system/etc/{firmware,lib}`（板上实测：`/etc/ohos_release` 不存在、`/system/etc` 存在、`ID=ubuntu`）。OH 特征收紧为只认 `/etc/ohos_release`，真 OH 硬件用 `MOIREGUARD_PLATFORM=ohos` 显式指定。
- **`make_camera()` 无声丢弃调用侧的 `--camera`**：OHOS 分支排在函数最前面直接 return，根本没看 `device`——与它自己的 docstring 相矛盾。改为“显式 `device` 为准、平台只挑默认源”，并保留两个 fail-fast（OH 无 device / OH + `/dev/video*` 仍显式抛未实现）。两个缺陷均入 `tests/smoke_board_pipeline.py` 新增的 §9（6 个用例，含反向护栏）。
- 上一轮 `RknnRuntime` / `export_model.py verify()` 的输出布局修正（本轮由产品路径二次确认）：`if out.ndim==4 and out.shape[1]==1: transpose` 在单通道模型上必命中，会把对的 NCHW 摇成 NHWC 且无报错；engine 侧 `_infer_padded` 的 `out[:ph, :pw]` 兜底碰到错布局会广播成静默错值——两处都是“错了也不报”的类型。

### Known Issues
- **真实负样本一个也没有**，判定层标定卡在摄像头上（明天接入）。
- **3.02 s/块为什么这么慢**未查（折合仅 0.12 TFLOP/s）：首要候选是 OneHot / 注意力 MatMul / reflect Pad / Resize 等算子落 CPU。
- **摄像头 V4L2 实取帧从未跑过**（本轮用 FileCamera）；int8 仍未转。
- router 判别力不足（BOARD_BRINGUP §13.3 第二层）依旧未解；但 §15.4 把它和判定层的问题分开了：就算路由全对，当前阈值下干净图也过不了“无摩尔纹”。

### 元教训
1. **探针验到的只是运行时边界**。“我的脚本能跑对”与“产品代码路径能用”之间隔着平台探测、适配器装配、参数传递。本轮两个缺陷都在 factory 装配阶段，探针天然绕过了它们。
2. **量纲命名要先于数字本身**。“单帧 2.9 s”写进三份文档后才被实测推翻——因为跑的永远是单块输入，“帧”这个字从未被定义。今后延迟必须写明“/块”或“/整帧（N 块）”。
3. **全部输入都报警不是阈值问题就是定义问题**。先从代码里查口径（训练/推理/上游脚本三处交叉证），再下结论；本轮若直接改阈值“修好”它，就是让判定层替转换链背了锅。

---

## [Unreleased] - 2026-11-07（第五轮续：转换链打通，但上一轮的归因只对了三分之一）

摄像头与 SD 卡尚未接入，本轮全部工作在 x86 转换环境 + 已连着的 RK3576 上完成。结果：**`SFR_best.pth` 第一次变成能在板端 NPU 上跑对数据的 `.rknn`**。

### Verified
- **单算子探针扩到 12 个并跑齐**（`build/_probe_ops.py`）：9 个 `building done`，NonZero 明确拒收，RangeEqual/ConstantOfShape 是探针自身的“输出全常量”伪阳性。⇒ **abort 不在单算子里**，BOARD_BRINGUP §13.3 的第一层归因不完整。
- **前缀二分定位**（`build/_probe_bisect.py`，1324 节点/1317 刀/单刀约 5 s）：轨迹 `666✘→336✔→501✘→418✔→459✘→438✘→428✘→423✘→420✔→421✘`，炸点 = 节点 #421 `/fission_module/Slice_1`，因其输入 `Pad(reflect)` 的 pads 走了 `Transpose→Reshape→Cast` 计算链、形状推不出来。
- **全图符号形状普查**（`build/_probe_shapes.py`）：`sfrnet_static.onnx` 1324 个张量里 **944 个含符号维度**（Conv 269 / Relu 215 / Concat 150 …）；`onnxslim` 折叠后降到 **8/2010**（只剩 `fission_module` 的 OneHot/Cast depth 维）。
- **转换成功**：`rknn.build(do_quantization=False)` 1 m 47 s、exit 0，产物 50,927,222 B。`E RKNN: Unkown op target: 0` ×5 出现但不致命。
- **板端真模型首跑**（`build/_probe_eval_x86.py` + `build/_probe_board_run.py`）：`load_rknn`/`init_runtime` 均 ret 0（runtime 仍是厂商 2.1.0，未动系统库）；输出 `(1,1,512,512)` raw logits；**NHWC float32 喂法与 onnxruntime 参照最大相对误差 0.0059、相关系数 1.0000**，NCHW 喂法相关系数 0.0321；单帧 **2.83–3.15 s**。脚本化流水线产物（`sfrnet_wired_fp16.rknn`，md5 与手工产物不同）重测数字一致。
- **静态化等价性**（上轮遗留的实测补完）：`build/static_frm_export.py` 4 图源 × 6 片共 24 片，最大相对误差 **0.000e+00**（逐 bit 等价）。

### Fixed
- **`rknn.config` 的 mean/std 口径搞错（本轮唯一的“完全错误”产出）**：直接把 ImageNet 的 0.485/0.229 烘进模型，而 rknn 的 mean/std **直接作用于喂入的张量、不会帮你 /255**（config 那行 `[0,255]` 警告就是这件事）。板端三种喂法输出量级全错（参照 mean -9.05 vs 板端 -68 / -13836 / -14076）。改为烘**恒等**预处理（`MEAN_BAKED/STD_BAKED`），int8 校准集随之改为“归一化后的 `.npy` 列进 txt”，保证校准域 == 推理域。
- **`RknnRuntime` 输出布局处理错**：实测 `.rknn` 输出的已经是 NCHW `[1,1,H,W]`，而旧代码 `if out.ndim==4 and out.shape[1]==1: transpose` 在单通道模型上必命中，会把对的弄成 `[1,H,W,1]` 且无任何报错。收紧为 `shape[1]!=1 and shape[-1]==1` 才转；`warmup()` 统一成与 `infer()` 同布局；`export_model.py` 的 `verify()` 同一处逻辑同步修。（输入侧 permute 本来是对的：模型头的 `framework layout: NCHW` 指源框架布局，不等于运行时输入布局——我一度拿它怀疑要点 1，怀疑错了。）
- **`_probe_ops.py` 的 `make()` 没有逐 case 容错**：一个探针导出抛异常就断了后面 9 个 case（上轮“只跑出 3/12”的真相）；同时修掉 ScatterND 探针在 batch=1 上索引越界。
- **`_probe_bisect.py` 把 SIGABRT 当成 exit 134**：`subprocess.returncode` 对信号是 **-6**，导致基线误判为“全图没复现 abort，前提不成立”。封装 `aborted()` 后二分才收敛。
- `export_model.py`：`rknn` 子命令现在会先数符号维度、非 0 自动跑 `onnxslim` 常量折叠（`--no-simplify` 留给取证）。

### Known Issues（本轮没解决，别读成“全部搞定”）
- **int8 仍未转过**：缺 100–200 张真实屏摄校准集（SDD §6 风险 #7 红线未动）。
- **单帧 2.9 s** 与任何“实时”叙事直接冲突，已开为 SDD §6 风险 #11；目标帧率（Q3）需用户定口径。
- **router 无判别力**（BOARD_BRINGUP §13.3 第二层）完全未解：静态化只保证“图上能算对”，不保证“选得对”。
- 板上 `/root` 现在有本轮产物（两个 .rknn + `_probe_board_run.py` + `eval/`）；仓库侧 `build/models/*.onnx|*.rknn` 与 `build/probes/` 均不入库。

### 元教训
1. **“最小模型验链”会掩盖整图级障碍**：Conv 探针上板全绿，却盖住了“导出产物形状不静态”这个真正的拦路问题。验链结论只能用于它直接覆盖的那一层。
2. **静默崩溃要用可二分的坐标系统去定位**，而不是用更多单算子测试堆经验：前缀二分 10 刀、约 1 分钟就落到具体节点，而单算子矩阵跑了 12 个一无所获。
3. **警告行优先于推论**：`[0,255]` 那行警告第一次出现时被我当成量化相关的噪音，其实它就是本轮唯一那个致命错误的书面答案。

---

## [Unreleased] - 2026-11-06（默认形态根本跑不出帧：适配器契约 + 错误恢复上限）

上一轮收尾时做全形态实跑，`python -m app.main`（不带任何参数，正是 README 承诺的板端形态）跑 12 s 得到 **8 299 254 字节日志、0 条 frame**。顺这条线挖出一串真缺陷，全部先取证再改，性能相关的取舍带数字。

### Fixed
- **`FakeCameraAdapter.grab()` 不收 `timeout_ms`**（Liskov 违背）：抽象基类与另外三个真实适配器都是 `grab(timeout_ms=3000)`，唯独假相机写成 `grab(self)`；引擎按 CONTROL_LOGIC 调用即 TypeError。开发机默认形态用的正是假相机 —— 裸跑一帧都出不来。
- **假相机的合成帧不是 HxWx3**：`np.where(stripe[:, :, None], 205, 50)` 里 `stripe` 只有 `(H,1)`，广播出来的“帧”是 `(H,1,1)`，到 `transpose(2,0,1)` 必炸（这条被上一条盖住，修完签名才露出来）。
- **启动自检信元数据不信像素**：STEP 6 只判 `data.size == 0`，于是 `(480,1,1)` 被报成「连通性自检通过: 640x480」。新增 `frame_shape_error()`（适配层），自检与每帧取帧后都按 `data.shape` 校，日志也改报真实像素尺寸。
- **未知异常既不退避也不升级（日志洪水的直接成因）**：`run_forever` 兜底分支先无条件打一行 ERROR，再交给 `_handle_error` 的 `else`（只置 DEGRADED）。CONTROL_LOGIC §6.1 早写明「未知异常：固定 2 s 退避、上限 3 次、达上限 FATAL、记完整堆栈」，代码没实现。补齐后同样的必炸路径从每秒数百行降到**恰好 3 条 ERROR**。
- **异常分类跑偏**：适配器抛的原生异常全被兜底成 unknown，C-10「重开摄像头」与 C-11「重载模型」永远走不到，重试上限也从 5/3 变成 3。`detect_once` 现在把 grab 的异常包成 `capture`、前向包成 `infer`、形状坏帧单独包成 `bad_frame`（重开摄像头和重载模型都救不了它）。
- **坏帧告警每帧一行**：`streak > 上限` 之后在全速循环里每帧刷一条 WARN。改为只在跨阈值那一次落日志。
- **`_fatal()` 里 `indicate()` 无兜底**：指示器抛异常会打断停机序列。两个真实实现都自行降级，但终端路径不该赌适配器守约。
- **FATAL 也返回退出码 0**：`run_forever` 跳出循环后总会走 `shutdown()`，状态名最后都是 SHUTDOWN，组合根分不出「正常收摊」与「恢复超限」。新增 `engine.fatal_reason`，`_run_loop` 据此返回 1。`moireguard.service` 是 `Restart=on-failure` 且 unit 注释明写「FATAL 由应用自身退出（退出码 1）」—— 在此之前，恢复超限的板子永远不会被拉回。
- `black_after` 的 docstring 谎称能验 C-12：全黑/全白判据代码里不存在（已 grep 确认逻辑层无一处）。改为写明它当前的真实作用；**是否补该判据留作待决**（它会改变交付布尔值，属决策面）。

### Changed
- `bootstrap()` 重跑时不再二次 `relay.start()`（新增 `_relay_started` 守卫）：未知异常恢复会重走启动序列，起第二个发送线程且旧连接无人回收。
- `run_forever` 的成功复位扩展到 `unknown`（与 capture/infer 同处清零）。

### 502 归因纠正（推翻上一轮写进本文件的结论）
上一轮 CHANGELOG 判定「同批 `urllib` 拿到的 502 未能复现，不予采信」。本轮该 502 在 `test_real_weights` 第 4 组复现，且机制查清：

```
no_proxy = *.local,172.31.*,172.26.*,172.17.*,10.*,192.168.*,127.*,localhost,<local>
urllib.request.proxy_bypass("127.0.0.1")  ->  False        # 实测
```

`urllib` 只把 `127.*` 当作 `*.xxx` 式后缀，**不匹配 IP 通配**，所以号称"已排除回环"的 `no_proxy` 对 `127.0.0.1` 无效，每个回环请求都先绕 `http://127.0.0.1:7897` 一跳。

> **本小节下面那句“502 与被测服务的鉴权逻辑无关”仍不完整，真正诱因见本节末尾
> 「401 可见性与 keep-alive 残体」：是我们自己的不排空 401 先把客户端弄成 broken
> pipe，代理只是把它翻译成了 502。代理确实在链路里（上面两条实测成立），但它不是根因。**

两条可复现证据说明 502 不可能出自我们的服务器：`grep "50[0-9]" app/adaptation/network/http_server.py`
只有 500 与 503，没有任何 502 出口；且 `server_version = "MoireGuard/0.1"`（同文件 118 行）是固定值，
代理返回的 502 带的是它自己的 `Server` 头。反向对照：空闲服务端、1 字节载荷，经代理连打 8 次全 401
—— 说明代理路径本身通。（当时据此说“那次 502 是代理侧偶发、诱因不查了”：方向对、体量错。
该实验用的是 1 字节 body，恰好落在套接字缓冲内，所以屏蔽了真诱因。）

- 处理：被测面（鉴权、状态码、SSE）断言全部改为 `ProxyHandler({})` 直连回环 —— `app/main.py` 的 `--selftest`、`test_real_weights` 第 4 组。`http.client`（第 5~7 组）本就不读环境变量代理，无需改。
- 判据强度不降：断言显式比 `e.code == 401`，代理污染只会让用例**失败**，不会被误读成通过。
- 元教训（写给后人，也写给改这条的我）：**"我试了一次没重现"不能否定"另一台机器上重现过"**。上一轮我只补测了"换端口返 200"就把 502 判死并写进正式记录，这是把一次抽样的运气当成结论。

### 401 可见性与 keep-alive 残体（把上面那条 502 追到底）
干净重跑真权重套件得 22/23，唯一失败就是「缺 token 被拒 — 502」——即上一轮被我判成
「未能复现、不予采信」的那一条。这回不靠抽样，直接做对照实验（各体量连打 6 次，全程
直连不经代理）：

| 未带 token 的请求体 | 修复前 | 修复后 |
|---|---|---|
| 空体 / 1 KB | 401 ×6 | 401 ×6 |
| **1 MB / 4 MB** | **URLError ×6（一个状态码都拿不到）** | **401 ×6** |
| 100 MB（超 `max_upload_mb`） | URLError | URLError（有意保留） |

机制不是代理，是 `_reject(401, ..., drain=False)` 自己的设计：`protocol_version = "HTTP/1.1"`
下先回响应不读体，体一旦大于套接字缓冲，客户端就在写入途中收到 FIN，拿到 broken pipe；
经代理时代理把这个上游中断翻译成 502——所以代理只是个报信人，不是制造者。

- 修：`_reject`/`_drain` 增加 `cap`，未鉴权路径排空上限压到 `max_upload_mb`（读取量不越过配置，
  但正常体量一定能看到 401）；超上限的声明体量仍不排空、直接断连。
- 修：新增 `handle_expect_100()`——鉴权不过就在传体前回 401、**不读一个字节**。这才是
  “不给身份不明者读数据”与“客户端要看得到状态码”的兼得点（不是 drain=False 那个旧取舍）。
- 修（同一类静默错失，顺着 grep 找到）：`/detect` 的两个 503 分支与 `do_GET` 的 401/404 也是
  不排空就回响应，全改为 `_reject`。实证：401 后在同一条 keep-alive 连接上发第二个请求，
  修前会读到残体错位，修后稳得 200（手机侧 okhttp / `requests.Session` 都复用连接，不是边缘场景）。
- 反证一个写进 docstring 的断言：“放行但不回 100”与“回 100”的实测对比为 **1.008 s vs
  0.048 s**（21×），所以授权分支必须交回 `super().handle_expect_100()`。

### 文献归档与安卓路线处置
厂商资料原先堆在仓库根目录（共 11 份 PDF：10 份 IDO 厂商文档 + 1 份论文），已归位：
`docs/vendor/purplepi-oh2/`（5 份：Linux 使用/开发手册、OpenHarmony 使用手册、开发板规格书、
上手指南）、`docs/vendor/som7609/`（3 份：S1/S2 核心板规格书、S1 硬件设计指南）、
`docs/literature/`（1 份论文，用 `git mv` 保留历史，三处引用已同改路径）。
共删 2 份：`IDO-Purple_Pi_OH2_Android使用手册.pdf`，以及 md5 与保留那份完全相同
（`9f8bb866…`）的重复下载 `Linux开发手册 (1).pdf`。现存厂商 8 份 / 26 MB，论文 1 份 / 21 MB。

> 安卓不在项目路线上，且不是“暂不支持”而是“整条依赖链不成立”：打包脚本落 `/opt` +
> systemd 单元、`rknn_runtime.py` 依赖 Python 的 `rknn-toolkit-lite2`、取帧走 V4L2 —— 在安卓
> 上全部要换成 init 服务 + NDK `librknnrt.so` + Camera HAL。所以不再收安卓手册，
> 后人看到这条就别再往回加。但出厂预装的安卓是**免费的设备取证机会**（`librknnrt.so` 版本定
> Toolkit2 对齐、dmesg 定 LPDDR 口径、cpufreq 定 A72/A53 基线），刷机前先拿完。

厂商 PDF 不入库。`.gitignore` 新增 `docs/vendor/purplepi-oh2/*`、`docs/vendor/som7609/*`
及对应的 `!…/.gitkeep` 例外（与权重目录同一约定；已用 `git check-ignore -v` 验证：8 份 PDF
全部被忽略、.gitkeep 与新增的 `docs/vendor/README.md` 不被忽略、仍会入库）。clone 后目录
结构保留，内容从厂商资料站自行下载。

新增 `docs/vendor/README.md`（入库）作为清单与出处索引：哪份手册讲什么、什么时候要翻、
去哪儿重新下载（厂商为触觉智能 Industio，官网 `www.industio.cn`，深层链接未逐个核实故不写）。
清单已双向核对磁盘：8 份文件全部在册，索引里没有磁盘上不存在的条目。

### 性能核对（先测后改）
| 改动 | 实测代价 |
|---|---|
| grab/infer 两处 `try/except` | 0：CPython 零成本异常，裸调用 44.4 ns vs 带块 41.9 ns（差异在噪声内），且每帧只设 2 次 |
| 每帧 `frame_shape_error()` | 三次 shape 比较，纳秒级；相对单帧 240 ms（640×480）~4.2 s（4K）可忽略 |
| 故障退避 2 s | 只在错误路径付，健康路径 0 |
| §5.3 全黑帧判据（**未实现，待决**） | 4K 全图 mean+std **97.6 ms**（占 4.2 s 的 2.3%）；1/8 抽样 **2.59 ms**（0.06%）。代价不是问题，问题是它会把「遮挡镜头」的输出从 `False` 改成「无有效结论」 |
| 未鉴权路径排空到 `max_upload_mb` | 只在错误路径付；带 `Expect` 的主流客户端根本不付（实测上传 0 字节、2 ms 得 401） |
| `handle_expect_100()` 本身 | 每请求一次 `_authorized()` 字符串比较，无额外 IO；不鉴权时反而省下一整个体的读取 |
| 授权上传不回 100 的代价（已避免） | 实测 1.008 s vs 0.048 s（21×）：`super().handle_expect_100()` 不可省 |

### 验证（开发机 RTX 5060 Ti，真实权重）
- `smoke_board_pipeline` **36/36**，耗时 3.7 s（新增第 7 组契约与恢复上限 11 条、第 8 组 HTTP
  错误响应 6 条；+1.5 s 全花在 6 MB 回环排空上，退避仍打桩不实等）
- `test_real_weights` **27/27**（第 4 组补体量梯度 401、keep-alive 复用、Expect 三条）
- `--selftest --port 8399` PASS（改为 `ProxyHandler({})` 直连后首次实跑，`/status`、`/healthz` 均 200）
- 裸跑对照：修复前 12 s / 8 299 254 字节 / 0 帧 → 修复后 rc=0、2 帧共 **511 字节**（`fake-000002/000003`，coverage=0.772624，240–396 ms/帧）
- 上一轮那条 README 承诺「以下每条都在开发机实测跑通」中的 `python -m app.main` 一行，到本轮才第一次真正成立

### 板端首次上电取证（推翻本文与 vendor README 的一处假设）
新增 `docs/guides/BOARD_BRINGUP.md`，它是 RK3576 实板第一次上电的原始数据**唯一留存**（含刷机即失的 SN/MAC）。
取证经 Windows 侧 `adb.exe` 从 WSL 驱动；出厂态为 Android 14 userdebug（`rockchip/rk3576_u:14/UQ1A.240205.004.B1/tbr09191354`，
kernel 6.1.141 #52 SMP PREEMPT，`verifiedbootstate=orange` 未锁定）。

- **推翻一条写进本文与 `docs/vendor/README.md` 的取证前提**：原话"`librknnrt.so` 的版本能定 Toolkit2 对齐口径"实测不成立 ——
  `/vendor /system /odm /oem /apex /data` 全盘搜 `*rknn*` 命中 0，`/vendor/bin` 无 `rknn_server`，`getprop` 只有五个 `*rockchip` HAL。
  出厂安卓根本没带 NPU runtime，Toolkit2 口径只能改从厂商 Linux SDK 拿。同一段里另两项（dmesg 定 DDR、cpufreq 定大小核）**成立**，
  所以取证路线整体仍值得做，而且不必"抢"——正常上电就能重连 adb，窗口每次都在。
- **纠正 SDD §1.1 一处 CPU 事实错误**：本文长期写"4×A72 @2.2GHz + 4×A53 @2.2GHz"。实测 `policy0`（A53×4，`CPU part 0xd03`）
  档顶 **2016 MHz**、`policy4`（A72×4，`0xd08`）档顶 **2208 MHz**。**A53 到不了 2.2 GHz**，软回退（`OnnxRuntime CPU` 落在 A53）
  的预算要按 2.016 GHz 重估。
- **LPDDR 口径冲突收窄但未结**：`dmc available_frequencies` = 534/1320/1968/2736 MHz，`normal_rate` 1968 MHz，`MemTotal` 3,990,676 kB。
  没有 LPDDR5 该有的 3200+ 档 → **"LPDDR5" 判死**；但板上无 DDR 类型节点（debugfs 未挂载、无 `/proc/ddrinfo`），LPDDR4 还是 4X
  仍不断言。上一轮 11-05 那条 Known Issue「需上电实测后回填 §1.1」到本轮**部分关闭**。
- **新增两项此前无依据的事实**：NPU devfreq **300–950 MHz** 且 governor `rknpu_ondemand` → 冷启动首帧跑低档，
  **任何延迟实测必须先 warmup 再计时**；eMMC 实测 `mmc0: new HS400 Enhanced strobe MMC card` → 5.1 口径坐实。
- **烧录路径由文档断言升级为实板确认**：厂商《上手指南》§八只有 USB 直连（Loader / `adb reboot loader` / Maskrom 三法），
  **无 SD 启动**；`SDDiskTool` 内部 `sd_boot_config.config` 自述 "rockchip sdcard boot config file **for factory**"，
  TF 卡在功能表里的用途写的是"测试传输8G文件"。首轮选型定为 `IDO_PurplePiOH2_V1B_Ubuntu22.04_HDMI4K_250907R.img`
  （zip 1,886,793,196 B / img 6,163,532,419 B / MD5 `8cdd8d825ea8976239a702b7586f3406`，`unzip -t` 全量 CRC 通过），
  拒绝 Xenomai/Preempt 与 Buildroot 的理由见 BOARD_BRINGUP §4。
- 摄像头接线：厂商手册确认板上 **3 路**座，**J19/J25 默认适配 IMX415，J20 是 OV13855（画面偏绿）**。插错不报错，
  属"能跑但结果是错的"，已写进验收清单。
- 串口：`/proc/cmdline` 的 `console=ttyFIQ0` → RK35xx 调试串口是 **1500000-8N1，不是 115200**。

### 仓库卫生（顺带做的三件事）
- `.gitignore` 新增 `Tools/`（4.8 GB：Windows 侧烧录/调试工具链 + VirtualBox + Ubuntu 22.04 ISO）与 `预编译固件/`
  （14 个 update.img 压缩包，19 GiB）。两者原本处于"未跟踪但也没被忽略"状态，一次 `git add -A` 就会把二进制永久
  写进历史。已用 `git check-ignore -v` 验证命中。
- `Tools/` 内 8 个 `(1)` 副本经 md5 逐条比对与正本完全相同，已删除（82 MB）。
- `ubuntu-22.04.5-desktop-amd64.iso.baiduyun.p.downloading` 经 ISO9660 主卷描述符核对**内容完整**
  （声明 2,325,541 扇区 × 2048 = 4,762,707,968 字节 = 实际大小），已去掉后缀重命名。它是 x86_64 PC 镜像，
  只服务 RKNN-Toolkit2 转换环境，**不是板端镜像，不能写进 SD 卡**。

### 元教训：`command not found` 不等于"功能被禁用"
本轮先下过"WSL 互操作被禁用、所以不能从这边驱动 Windows adb"的结论，依据只是 `cmd.exe`/`powershell.exe` 找不到。
实测三项证据（`/etc/wsl.conf` 里 `enabled=true`、binfmt `WSLInterop` 为 `enabled`、`WSL_INTEROP` 在位）证明**互操作一直是开的**，
关的只是 `appendWindowsPath=false`，绝对路径 `/mnt/c/Windows/System32/...` 从头就能跑。代价是：差点把"取证"这项一次性动作
推回手工，并把一条错误的环境结论写进文档。**判"某功能不可用"之前先去找它的开关本身，不要用一次调用失败反推配置。**

### 烧录前复核（把状态从"待烧"推成"只差点【升级】"）
- 镜像完整性**四处一致**：`unzip -t` 全量 CRC → 解压后 `md5sum` → 与包内 `MD5.txt` 及厂商 PDF 声明互验
  → 复制到 `D:\rkflash\` 后用 Windows `Get-FileHash` 再算一遍。上轮欠的"解压后比对 MD5"已清。
- **差点误判 V1B**：板上设备树自报 `Industio EVB7609 V1 Board`，一度被当成与 `_V1B_` 包冲突的证据。
  查《Linux 使用手册》修订史才发现两件事：轴不同（"V1" 是 EVB 底板名，V1A/V1B 是 OH2 的 PCBA 版次），
  且时间线对得上（文档自 V1.3/2025-08-25 起从 V1A 换 V1B，而板上出厂 Android 构建于 2025-09-19）。
  **决定性证据**：待烧镜像 dtb 内的板级串 `Industio-EVB7609V1-VS1.0.0-250716` 与板上现行固件逐字符相同。
  另：手头 14 个包全部标 `_V1B_`，根本不存在 V1A 备选——所以真正要验的是"这批包对不对"而不是"选哪个"。
- 新增板端实证（均入 BOARD_BRINGUP）：**eMMC 容量 57.6 GiB**（标称 64 GB）——"4+64G" 从订单口径升级为板上实测；
  出厂 Android 的 15 个 by-name 分区（含 `super`/`dtbo`/`vbmeta`）；从 `.img` 里 grep 出的目标分区表：
  扁平 8 分区、`rootfs:grow` 起始 2416 MiB、**`userdata` 固定 2 GiB**。最后一条有设计含义：
  要 persist 的数据一律落 rootfs（`/opt/moireguard`），不要往 userdata 写。
- Windows 侧就绪：`D:\rkflash\` 下放了校验过的 `.img`、`RKDevTool.exe`、`DriverInstall.exe`。
  `adb reboot loader` 可**免按 Recovery 键**直接进 Loader，十几秒后 Windows 就枚举到新设备。
- ⚠️ **上一轮刚写下的"驱动本来就装好"当场被证伪**：那个结论只看了 ADB 态（`PID_0006` / `rk3576_u` / Status OK）。
  一进 Loader，板子变成**另一颗设备** `PID_350E`（`USB download gadget`），Windows 报
  `CM_PROB_FAILED_INSTALL`（代码 28）。**ADB 驱动与 Loader/Maskrom 驱动不通用**，"能 adb"推不出"能烧录"——
  差点在 RKDevTool 里"看不到设备"上白耗一轮，已写回 BOARD_BRINGUP §5 步骤 1。
- 两个驱动相关的纠正：① 代码 28 **不等于"机上没这个驱动"**——`pnputil /add-driver rockusb.inf /install`
  回的是"已成功添加。（系统中已存在）"，驱动包本来就在仓库里，缺的只是**绑定到这颗设备**；
  绑定后变 `Rockusb Device` / Status OK。② 厂商 `DriverInstall.exe` 从 WSL 互操作 `Start-Process`
  会**静默退出**（日志空、什么都没装），不如一行 pnputil 直接解决。

### 元教训：一次性提取的数据当场落盘，别信临时文件
上轮用 `mutool` 抽出的 PDF 文本存在工作区外的路径，两次会话之间开发机漂移过：根 fs 从 `overlay 98 G`
变成 `/dev/sdd 1007 G`、`/mnt/f` 整卷消失、沙箱内 `/tmp` 变只读，**临时文件全部丢失**，同一批 PDF 只能重抽
（且 `mutool` 版本变了，`-F` 现在必须带格式参数，改用 PyMuPDF）。原计划把镜像放 F: 盘也因此改投 D:。
规则：**从不可重下的资料里提取出的结论，当场写进被跟踪的文档**；工具链不要依赖未入库的中间产物。

### 烧录完成，取证路线收口（板端 Ubuntu 态实测）

【升级】已执行，出厂 Android 被替换。完整数据在 [BOARD_BRINGUP §10](../guides/BOARD_BRINGUP.md)，这里只记影响决策的部分：

- **判别“烧成功没”不用看界面**：`adb devices -l` 的身份字段从 Android 态的 `rk3576_u` 变成
  `product:occam model:Nexus_4 device:mako`（Rockchip Linux 镜像 libcomposite adbd 的默认 ID）。
  SN `98fed70982ab4c63` **跨刷机存活**，没动用 RKDevInfoWriteTool。
- **`/usr/lib/librknnrt.so` = 2.1.0** —— 上轮“出厂 Android 搜不到 runtime”的缺口在官方
  Ubuntu 固件上补齐，Toolkit2 对齐口径正式定为 **2.1.0**（取证时要分系统做，不是“安卓路线不行”）。
- **连带查出一个会真咬人的代码缺陷**：`build/export_model.py` 的 `--target` 缺省值仍是
  早期误记的 `rk3588s`（函数默认值、argparse 默认值、docstring 示例三处都是），**照现有文档
  原样敲命令就会导出错平台的模型，板上 `rknn_init` 必挂**。三处已改 `rk3576`；
  `build/README.md` 钉上 `rknn-toolkit2==2.1.0`，部署示例用户名 `firefly` → `industio`（板上实测只有
  `root` 与 `industio`）。
- **容量与分区推算命中**：rootfs 分区 59,332,607,488 B = 55.25 GiB、`/userdata` 固定 2 GiB、
  RAM 可见 3895 MiB。“要 persist 的数据写 `/opt`（rootfs）而不是 userdata”这条约束成立。
- **跨系统对比延迟时要注意的两个差异**：CPU governor 是 `schedutil`（Android 态不是），
  NPU 仍是 `rknpu_ondemand` —— 所以 **warmup 不是刷机可绕的开箱问题**。
- **首帧实测现在阻塞在硬件**：`imx415` 驱动已注册（`driver version 00.01.08`、`detect lane 4`），
  但强制 bind 读到 `Unexpected sensor id(000000), ret(-5)` —— I2C 上没人应答，**模组未接**；
  V4L2 栈本身正常（73 个 `video*`、`media0-8`、`v4l2-ctl` 可用）。为判别此事我写过一次 sysfs bind，
  probe 失败因此未建立绑定，状态可逆（§10.1 已交代）。
- **推翻 SDD 的一处隐含假设**：官方固件里 `numpy`/`cv2`/`rknn-toolkit-lite2` **一个都没装**（只有
  Python 3.10.12）。“随镜像预装”是我们构建镜像那一步的责任，不是拿到官方固件就自动成立。
- 新增交付安全项：`adb shell` 直接给 root 且 adbd 开机即跑。量产/外场交付前必须关。

### 元教训：改了文档就要 grep 到代码侧

上一轮把芯片型号从 RK3588S 更正为 RK3576 时，**只改了 `.md`**。`export_model.py` 里那个被代码
消费的默认值没动，于是仓库处于“文档说 3576、执行链默认 3588S”的分裂态，而且不报错——直到
拿到板上才会以 `rknn_init` 失败的形式爆出来。规则：**更正一个会进入命令/配置的常量时，
必须 grep 到代码与脚本，不能停在文档**；默认值比注释危险，因为它在没人看的时候也会生效。

### 联网（板上有 WiFi）与一次自我推翻

用户指出板上带 WiFi 模组后，上一小节刚写的“网络接入未解决”立即作废。实测全在
[BOARD_BRINGUP §11](../guides/BOARD_BRINGUP.md)：

- **链路已通并持久化**：芯片 **BCM43456**（走 SDIO + `dhd` fullmac，不是 `brcmfmac`），关联
  `Xiaomi_432` 的 **5G ch36、−50 dBm**，DHCP `192.168.31.53/24`，NM 自动生成 profile 并开机自连。
  外网：网关 7.3 ms、`223.5.5.5` 26 ms、`apt-get update` 退出码 0。
- **部署路线解锁**：开发机 WSL `eth3 192.168.31.6/24` 与板子**同网段**，拿到 `SSH-2.0-OpenSSH_8.9p1`
  横幅 → `build/README.md` 里的 scp/ssh 从“纸面流程”变成可执行（用户名已实测为 `industio`）。
- **板端依赖现场补齐**：`python3-numpy 1.21.5`、`python3-opencv 4.5.4`、`iw`、`rknn-toolkit-lite2 2.3.2`
  已装。同时明确：**现场能装不等于交付形态合格**，SDD §1.2 那行已补上“必须固化进镜像”。
- **CLM blob 缺失查到根因**：`/lib/firmware/` 有 `fw_bcm43456c5_ag.bin` 但**没有**对应 CLM
  （只有四个其他芯片的）→ 厂商打包漏件。真实代价不是报错而是 `iw reg get` 给出
  **`country 00: DFS-UNSET` + 5G 全段 PASSIVE-SCAN**；本次能连不等于无风险，**也没拿别的芯片的 blob 冒充**。
- **推翻了本会话上一步写进仓库的结论**：“板上 runtime = 2.1.0 → 转换端钉 `rknn-toolkit2==2.1.0`”。
  实测发现 **PyPI 上 `rknn-toolkit2` 只有 2.2.0/2.2.1/2.3.0/2.3.2/2.5.0，`lite2` 只有 2.3.0/2.3.2/2.5.0，
  两个都没有 2.1.0** —— 那条“指令”根本无法执行。已回改 SDD、`build/README.md`、`export_model.py`
  三处，§9 该行重新打开。另实测：lite2 wheel **不自带** `librknnrt.so`（dlopen 系统的），而 2.1.0
  导出了 `rknn_init`/`rknn_create_mem`，所以符号层无缺口；但 `get_sdk_version()` 必须经
  `init_runtime` 后才有值 ⇒ **没真模型就不能定论**。内核侧 `RKNPU driver: v0.9.8`（未拿官方
  配套表坐实，不作断言）。推荐先转一个 2.3.2 模型直接上板试，成则维持 2.1.0，失败再升级
  `librknnrt.so`（升级属替换厂商系统库，已归入用户决策面）。
- 取件通道实测：板上 `github.com` 直连超时（HTTP 000）但 `api.github.com` 返 200；开发机直连不通，
  **经本地代理 `127.0.0.1:7897` 返 `200 Connection established`** → 老 wheel 可经开发机倒进板。
- **凭据纪律**：WiFi PSK 只在一命令里用一次，**未写入任何文件、文档、提交或长期记忆**。

### 元教训：实测值不等于可执行指令

“读出 2.1.0”是真事实，“所以钉 2.1.0”是把事实直推成指令，中间那一步（**这个版本到底能不能取到**）
我省了。同型错误在工程文档里很常见：正确的前提推出不可执行的命令。规则：**每条写进仓库的
操作指令，都要当场验证它的可执行性**（包是否存在于源、渠道是否可达、命令是否存在于板上的
busybox/dash）——本轮“板上 `/bin/sh` 没有 `time`”就是同一类小事。

---

## [Unreleased] - 2026-11-06（接通广播出口：常驻循环 → SSE）

上轮合并后复核技术流程时发现：`IResultSink` 只在适配层声明、`HttpNetServer` 也实现了 `publish()`，但 `engine._publish()` 只把结果存进 `_last_valid` 从不外推——广播面是**画在图上没接线**的。本轮把它接通，并修掉验证过程中撞出的限帧路径缺陷。性能优先的取舍全部带实测数字。

### Added
- **常驻循环 → 广播出口真正接通**：`DetectionEngine(..., sinks=[...])` 与后置 `attach_sink()`（组合根要用它解开「服务要引擎做 `/status`、引擎要服务做广播」的引用环）。逐帧判定现在会构造 DTO 推给 SSE
- **新形态 `--serve-net --loop`**：常驻检测循环与网络出口同跑。`/status` 读真实状态机（新增 `DetectionEngine.engine_state()`），`/stream` 收实时帧。该形态**故意不提供 `/detect`**（返回 503 `E-NO-UPLOAD`）：接上传检测要再起一个推理实例，等于板端把权重与 NPU 上下文加载两遍
- 广播的 DTO 只带标量：`mask_png`/`prob_png` 恒为 `None`，需要图像的客户端仍走 `/detect` 的 `X-With-Mask` 显式 opt-in

### Changed
- `Decider.decide(logits, want_mean=False)`：`mean_prob` 这趟全数组归约**只在有人订阅时才算**（2.9Mpx 实测多付 2.43 ms/帧，占单帧 0.06%）；`Decision` / `DetectionResult` 相应新增字段
- 限帧与常驻合并为同一个循环体：`run_forever(req, max_frames=N)` 取代 `main.py` 里手写的 `for _ in range(N): detect_once()`
- `IFrameRelay.push` 不再引用逻辑层类型，改由适配层声明 `IFrameResult` 协议（只读 `has_moire`/`coverage`）。原先靠 `TYPE_CHECKING` 兜住、运行时不构成依赖，但源码确有向上引用；改后 SDD「适配层不得 import 逻辑层」这条红线字面成立

### Fixed
- **`--frames N` 限帧运行从不广播、也不复位重试计数**：`main.py` 自己手写 for 循环直调 `detect_once()`，绕过了 `run_forever` 的循环体（`_publish`、`_retry` 归零、`DEGRADED→READY` 恢复）。这条是验证新 SSE 时撞出来的——订阅 60 s 事件数为 0，才顺藤摸到限帧路径根本没进循环
- SDD §4.5 R1 措辞自相矛盾：标签写「**丢新不堵旧**」，同一句解释却写「新帧到来覆盖旧帧」（即丢旧保新）。代码语义明确是 latest-wins，已改对标签与理由
- SDD R1 引用的 `drop_policy="latest"` 在代码里并无此参数——它其实是 CONTROL_LOGIC §3.1 的**规格名**。已在两处补注：实现上不存在帧队列，到点直接 `grab()` 取当下最新帧，「latest」是默认且唯一行为
- SDD「文档声称 vs 实际状态」缺口表的「网络出口」一行随实况更新

### 验证（开发机 RTX 5060 Ti，真实权重 + 真实样张）
- `smoke_board_pipeline` **19/19**（新增第 6 组广播出口）、`test_real_weights` **17/17**（新增第 5 组循环+HTTP 同跑端到端）
- `--serve-net --loop --camera samples/moire_photo.jpg --frames 2`：SSE 实收 2 条，`source_id=file-000002/000003`、`coverage=0.997286`（与循环日志逐位一致）、`max_prob=0.944891`、`mean_prob=0.8493`；进程退出后端口关闭（`finally: server.stop()` 生效）
- `/healthz` 免鉴权 200；`/status` 报 `DETECTING`/`READY` 真实状态且 `sse_clients` 随订阅变化；`/detect` 返 503 `E-NO-UPLOAD`
- 扇出开销实测：`_publish` 0 sink = **0.25 µs/帧**（纯早退）、1 sink = 12 µs、3 sink = 29 µs
- 曾考虑的 sigmoid 降 float32 提速**经实测否决**：`decide()` 全程仅 32 ms/帧（4.2 s 帧时的 0.8%），省十几毫秒不足以补偿改动交付判据数值口径的风险
- 排障记录（避免后人重踩）：第一次探测时 `/stream` 收到 0 事件，同批 `urllib` 请求还拿到 502。0 事件是真缺陷，根因是上面那条 `--frames` 限帧路径根本没进循环体——已由第 5 组回归锁住。
  - **本条对 502 的原判（"未能复现、不予采信"）已被下一轮推翻，见该轮 "502 归因纠正"**。当时的"验证"只测了"换个端口就返 200"，据此宣布与本功能无关；但 `127.*` 这种写法 `urllib` 根本不认（`proxy_bypass("127.0.0.1")` 实测返 `False`），请求确实每次都经代理一跳，502 来自代理而非被测服务。教训：拿一次"没能重现"当"不成立"，和拿纸面印象当结论一样不可信。

---

## [Unreleased] - 2026-11-06（合并 origin/main 的 `app/` 体系）

本地 ahead 1 / 远端 behind 4，双方各自实现了同一批抽象。合并策略：**以远端 `app/` 骨架为主干**（它符合 SDD §5 且有状态机与冒烟测试），**迁入本地的依赖倒置纪律与网络出口**，并修掉实测出的远端缺陷。合并全程 `--no-commit`，安全点留在分支 `backup/pre-merge`。

### Added
- 真实权重回归 `tests/test_real_weights.py`（13 项）：`TorchRuntime` 契约、`DetectionEngine` + 真实权重端到端、`Decider`/`PostProcessor` 边界同判、HTTP `/detect` + 401 + `X-With-Mask` opt-in。动机是冒烟测试 13 项全用 `FakeRuntime`，"后端接口与真模型对不上"这类缺陷一条也抓不到——合并时正好撞上
- `app/adaptation/base.py` 迁入 DTO（`DetectionResultDTO` / `DetectionEvent`）与反向注入协议（`IImageService` / `IDetectionEngine` / `IResultSink`）、`NetworkConfig` / `NetServerOptions`
- `configs/default.yaml` 新增 `runtime` 节（板端 rknn + `runtime.host` 开发机覆盖层），`camera` / `relay` 节入 `logic_layer/config.py` 类型化（`CameraConfig` / `RuntimeConfig` / `RelayConfig`）
- `app/main.py` 重写为**唯一组合根**（336 行）：`build_config` 四段合并（dataclass 默认 ← `configs/default.yaml` ← 平台持久配置 ← 命令行），四种形态 = 板端常驻循环 / `--detect` / `--serve-net` / `--selftest`
- `serve.py` 保留为 22 行兼容壳，参数原样转发，不再承接逻辑

### Changed
- **包名回到 `app/`**（与 11-05 那条 `app/ → moireguard/` 形成往返）：远端按 SDD §5 用 `app/` 落地，两套三层并存必然继续漂移，`moireguard/` 源码已全部 `git rm`
- 默认值真源从三处收敛到一处：`bootstrapper.DEFAULTS` 改为由 `config.AppConfig` 的 dataclass 导出（此前 `config.py` / `bootstrapper.py` / `configs/default.yaml` 各写一份且模型路径不同，开发机永远拿错路径）
- 适配层不再 import 逻辑层（`torch_runtime.py` 原借用 `logic_layer.exceptions`，违反 DIP 红线）
- 平台判定改为按"是否装了产品镜像"识别（`/etc/moireguard`、`/usr/share/moireguard`、`rknnlite` 可导入），开发机可用 `MOIREGUARD_PLATFORM` 强制；此前按 `sys.platform == "linux"` 判定，WSL 被误当板端去读 `model.rknn`
- `factory.make_runtime("auto")` 去掉 `rknn→onnx→torch` 软回退：选定即定死，缺失显式报 `E-BACKEND-UNAVAILABLE`（板端静默跑 CPU torch 会让"NPU 已就位"的结论失真）
- 目录树真源唯一化：SDD §5 为唯一一份，`docs/ARCHITECTURE.md` §6 与 `README.md` 改为指针
- `docs/api/README.md` 网络出口 4 端点、鉴权与掩膜 opt-in 行为入档（不再是纯占位）

### Fixed（远端缺陷，均有实测复现）
- `TorchRuntime` 把 `registry.create()` 返回的 `DetectorCore` 当 `nn.Module` 调用 → `'SFRNetDetector' object is not callable`，真实权重路径 100% 崩溃（改为 `core.forward(t)`）
- `load(model_path)` 丢弃入参不用 → 配置里的权重路径成了死字段（现真传递，并做绝对/工作目录/仓库根三级定位）
- `bootstrapper` 把任何加载异常一律标 `E-WEIGHT-NOT-FOUND` → 一次 `TypeError` 被报成"权重找不到"，直接带偏排障；现按 `FileNotFoundError` / 已带 `E-*` / 其余归 `E-RUNTIME-FAIL: 类型名` 分类
- `app/main.py` 的 `--model` 在 `bootstrap()` **之后**赋值 → 对首次加载是死标志；现经 `engine.bootstrap(overrides)` 在加载前生效
- `detect_once` 只用构造期的 `Decider()` → `req.threshold` / `req.coverage_threshold` 被静默忽略；现按请求重建
- `app/adaptation/__init__.py` 只 re-export `factory` 模块 → `ada.detect_platform` AttributeError，说明该入口从未跑起来过；现补齐工厂函数与抽象类导出
- 开发机日志全静默：`HostDeviceIO` 无 handler，`logging.lastResort` 只放行 WARNING 及以上，"跑完一声不响"比报错更难查；现挂 stdout handler，并置 `propagate = False`（修 `run_sim.py` 每条日志打两遍）
- `factory` 引用的模块名不存在（`camera/v4l2.py` 案例名、`FileCamera` 类名）→ `ModuleNotFoundError` 而非可读错误
- 存活探针 `/healthz` 被鉴权闸门拦住：配 `network.token` 后探针拿到 401（实测），等于让监控系统把健康服务判成"服务已死"。现 `/healthz` 免鉴权（不触发推理、不回诊断数据），`/status`、`/stream`、`/detect` 一律仍过 token。`--selftest` 因不配 token 照不到这条，已在 `tests/test_real_weights.py` 补两条断言（免鉴权 200 / 诊断面仍 401）
- `FileCamera` 的 `frame_id` 前缀 `sim-` 出现在产品形态日志里（`python -m app.main` 常驻也打 `frame=sim-000002`），与附录 B 的逐帧日志契约不符；改为按来源类型命名 `file-`（与 `fake-`、V4L2 的毫秒时间戳同口径）
- 文档事实：`build/README.md` 板型 RK3588S → **RK3576**（远端新增文件里的漏改，全库 RK3588 已清零）；`docs/README.md` 章节指针纠错（两个预留接口在 §4.2 非 §5，逻辑层在 §4.3 非 §6）与不存在的 `guides/*.md` 清单；SDD §5 树按现实重写并列出 5 条"文档声称 vs 实际状态"缺口（`supervisor.py`、`build/calib/`、`image_ohos/`、`ohos_io.py`、网络出口未立项）；`upstream/` 路径引用（`utils/` 已改名）；两处指向不存在的 `Supervisor` 的代码注释

### Removed
- 根目录 `SOFTWARE_DESIGN.md`：11-05 已移动到 `docs/design/`，但远端 4 个提交里的 `eccf5dc` 又把 v2.0→v2.3 的升级写回了这份**老副本**，导致两份 SDD 并存且根副本仍是 RK3588S 与已证伪的 1.5 GFLOPs 推算。逐块 diff 确认根副本无任何独有内容（7 处差异全是本地更正的旧态），故删除、保留 `docs/design/` 一份
- `.merge_scratch/`（合并期临时留存的 4 个旧文件副本）

### 验证（开发机 RTX 5060 Ti / py3.12 / torch 2.13+cu130）
- `tests/smoke_board_pipeline.py` **13/13**；`tests/test_real_weights.py` **13/13**
- 数值跨栈一致，三条独立路径互证同一张摩尔纹样张：板端常驻循环 `coverage=0.997286 / tiles=32`、`run_sim.py` 两帧 `0.997286`、`--detect` `0.997296`（与合并前 `serve.py --detect` 完全一致）；IDE 截图 `0.981504`
- `--selftest` PASS（`/status` 200、`/healthz` 200）；`--serve-net` 常驻进程实测：无 token 的 `/healthz` 200、`/status` 401、`/detect` 401，带 token 的 `/detect` 返 `coverage=0.997296` + `mask_png_b64`（与 `--detect` 同值）
- README 里列的 6 条命令本轮全部实跑一遍（`--detect` / 常驻循环 / `--serve-net` / `--selftest` / `run_sim.py` / `serve.py` 壳），不写未验证的命令

---

## [Unreleased] - 2026-11-05

### Added
- 创建 `docs/` 文档目录结构
- 添加 `ARCHITECTURE.md` 架构概览文档
- 添加 `CHANGELOG.md` 变更日志
- 创建 `moireguard/` 软件主体框架
  - 模型层: 接口契约 + 默认适配器 + 注册表
  - 逻辑层: 推理服务 + 滑窗引擎 + 后处理器 + 导出服务
  - 控制层: 占位 (待 M3 阶段实现)
- 创建 `configs/` 配置文件目录
- 移动权重文件到 `assets/weights/`
- 添加 `requirements.txt` 和 `pyproject.toml`
- 建立适配层 `moireguard/adaptation/`（v2.2 §4.1 接口先行）
  - `base.py`: `Frame` / `DetectionResultDTO` / `DetectionEvent` + `ICameraAdapter` / `IRuntimeAdapter` / `IDeviceIO` 抽象，以及反向注入的 `IImageService` / `IDetectionEngine` / `IResultSink` 协议（Protocol 而非 import，保证适配层不依赖逻辑层）
  - `factory.py`: 平台探测与装配，全项目唯一允许平台分支处；未实现的后端（onnxruntime / rknn）显式 `raise NotImplementedError`，不静默降级
  - 首组实现：`runtime/torch_runtime.py`（含 warmup 治理）、`camera/fake.py`（可注入故障）、`device_io/linux_io.py`
- 新增网络出口 `adaptation/network/http_server.py`（纯标准库 `ThreadingHTTPServer` + SSE）：`GET /healthz|/status|/stream`、`POST /detect`，Bearer 鉴权与体积闸门，`publish` 非阻塞丢慢客户端
- 新增组合根进程入口 `serve.py`（`--serve-net` / `--detect` / `--selftest`）；`configs/default.yaml` 新增 `network` 节（`enabled: false` 默认关闭，无头形态下网络为显式 opt-in 旁路）

### Changed
- 移动 `SOFTWARE_DESIGN.md` → `docs/design/`
- 移动 `TECHNICAL_DOC.md` → `docs/design/`
- 移动 `SFRNet大创创意方向方案.md` → `docs/`
- 移动 `SFR_best.pth` → `assets/weights/`
- 重命名 `app/` → `moireguard/` (与软件代号对齐)
- 重命名 `utils/` → `upstream/` (上游代码隔离，语义更清晰)
- 更新所有 Python import 路径引用
- 对齐 SOFTWARE_DESIGN v2.2 的无头镜像形态：`README.md` 定位/特性/目录树重写，`docs/ARCHITECTURE.md` 加“已被 v2.2 取代”差异表（不重写正文）
- `moireguard/logic_layer/__init__.py` 红线从“不做 UI 回调 / 不持有 Qt 对象”改为“不 import 任何平台 SDK / 不碰 state_dict / 不做平台判断”

### Removed
- 删除 `moireguard/control_layer/`（`__init__.py` + `widgets/` + `viewmodels/`，共 32 行纯占位，代码中零引用）—— v2.2 目标架构为适配层/模型层/逻辑层，无 UI 层
- 删除 `moireguard/resources/`（仅 `.gitkeep`，UI 时代资源占位，全库无引用）

### Fixed
- `registry.py:_register_builtins` 不再静默 `except ImportError: pass`（原注释“SFRNetDetector 尚未实现”与代码不符；真实危害是依赖缺失时退化成“注册表为空”，把环境问题伪装成配置问题）
- `predict.py:14` 权重路径 `./checkpoints/ins/SFR_best.pth` → `./assets/weights/SFR_best.pth`（M0 权重迁移后的遗留断链）
- `logic_layer/__init__.py` 改为 PEP 562 惰性导出：原先急切 re-export `InferenceService` 使“只导入配置/异常类”也拖起 torch，无 torch 环境下无法启动适配层；公开 API 不变
- `adaptation/network/http_server.py` 两处缺陷：`/status` 处理函数闭包误引用 Handler 实例的 `self`（改为捕获局部 `started_at` 与共享的 `shutdown_flag`，二者已在 `HttpNetServer.__init__` 预先初始化）、无效占位串 `"UN装配"` → `"UNASSEMBLED"`
- **体积闸门失效**（端到端实测才暴露）：`_read_body` 回 413 时不消费请求体，keep-alive 下残留字节被当成下一个请求；urllib 默认发 `Connection: close`，服务端先响应后关连接时仍在写的客户端看到的是 `Connection reset` 而非 413——实测 12 次超限上传只有 2 次拿到状态码。新增 `_reject(code, payload, drain=True)` 先按 Content-Length 分块排空（受 `_DRAIN_HARD_CAP` 512MB 约束）再回响应；顺序不能倒，先发后排仍剩一半失败。未鉴权的 401 传 `drain=False`，不为身份不明的发送方白读大块数据。修复后 urllib 与 http.client 各 12 次均为 12/12 拿到 413
- `docs/design/SOFTWARE_DESIGN.md` 目标硬件由误记的 **RK3588S** 更正为实板 **RK3576**（§1.1 硬件表、软回退行、§2 选型表与性能预估、Q1/Q6），并同步修正派生事实：CPU 改 4×A72@2.2GHz + 4×A53、软回退核改为 A53、NPU “三核”不作断言（公开资料口径不一致）、eMMC 5.1、ISP V3.9，新增 RGA 可卸载 4K 预处理的事实依据；保留 6 TOPS 推算但标注 CPU 代际下降需下调软回退与融合/后处理预算
- 接实购订单页进一步更正 §1.1：新增“视频输入（多路 AHD）/ 视频解码（4K@120）/ 网络（双千兆）”三行；内存行不再断言 LPDDR4X（商品主图标 LPDDR5，与系列规格页口径冲突，改为标注冲突并要上电用 `dmesg \| grep -i ddrc` 实测）；摄像头行记入已购 IMX415 模组；Q2 由开放项改为“基本已定”；性能预估段补“IMX415 本身是 4K 级，112 块是常态负载而非最坏情况”
- `docs/SFRNet大创创意方向方案.md` 4 处 RK3588 按实购板型与实价更正（L85 选型、L147/394 离线版与实拍描述、L435 成本表：OH2 4+64G 整板 ¥999、核心板+IMX415 ¥200，已各购 2 件）

### Changed
- 开发机（RTX 5060 Ti，torch 2.13+cu130，py3.12）首次跑通真实推理链，`SOFTWARE_DESIGN.md` 性能预估段按实测重写：**单块 512×512 实测 178.6 G-MAC ≈ 357 GFLOPs，本文早期假设的 1.5 GFLOPs 低估约 238 倍**；单块 163 ms（batch=8 仅 138 ms/块→算力/带宽受限；fp16 119 ms），4K 全图 112 块端到端 20.1 s（融合+两次 PNG 编码仅占 6%）；对 RK3576 推论为 120–200 ms/块、**4K 一帧 13–22 s**，“5–20 ms/块、0.6–2.2 s/帧”作废，当前架构板上做不到实时
- Q5 阈值项补充实测证据；新增 **Q9：SFR_best.pth 是否已退化**（优先级等同 Q7）；收敛方向按实测热点重排，结构化裁剪砍 `d3` 升为首位（最贵 6 层里 4 条在 `d3.*.safm` 与全分辨率 `c1.2`）

### Verified
- 端到端真实链路跑通：`serve.py --detect` 与 HTTP `POST /detect`（真权重）均返回 200，返 `coverage=0.9960 / max_prob=0.9037`，`X-With-Mask` 带回 4.6 KB 掩膜 PNG；`/status` 报 `engine_state=READY`；无/错 token 均 401；坏字节干净地返 `E-DETECT-FAILED`
- 权重加载确认生效：`strict=True` 未报错，且随机初始化 mean 0.438 vs 加载后 0.903（不同）；`registry.py:102` 已调 `eval()`，预处理与训练侧一致（均为 `ToTensor` + RGB，`builtin_adapter.py:114` 除 255）

### Known Issues
- 适配层已抽象但未完整装配：`logic_layer/{engine,bootstrapper,decider,supervisor}.py` 仍缺失，`serve.py` 的 `ServiceFacade` 只是协议适配器，缺口 D-07 保持开放
- `build/` 目录仍不存在，`pth → ONNX → .rknn` 转换链（Q7）未跑通；RKNN-Toolkit2 只发 x86_64 wheel 且 `cpXXX` 严格绑 Python 版本，需在开发机（建议 Docker Ubuntu22.04 + py3.10）完成，不在板上编译
- 网络层尚无单元用例（`tests/unit/test_net_http.py` 未写），`--selftest` 仅验端点/鉴权/生命周期
- 网络出口尚未在 `SOFTWARE_DESIGN.md` 立项：§1/§4.4 对外的承诺仍只有一个布尔量（`IDeviceIO.indicate`），HTTP/SSE 属代码先行能力，是否升为正式输出通道待与 Q4 一并定夺
- LPDDR5 / LPDDR4X 口径冲突未解（商品主图 vs 系列规格页），需上电实测后回填 §1.1；双千兆网口与 AHD 输入均未进入任何接口设计
- 上游 `train.py:24` 的 `autocast(device_type='cuda:1')` 硬编码问题
- 上游 `predict.py:42-43` 小图推理崩溃问题
- 上游 FRM Router 100% 坍缩到 branch_1

---

## [Unreleased] - 2026-11-06（第五轮：转换链跑到了板子上，但阻塞换了名字）

用户让“进行下一步”（摄像头与 SD 卡明日接入），于是把 §11.4 欠的转换判定实验一次跑完。
结果：**版本链清了，FRM 挡住了**。详见 [BOARD_BRINGUP §13](../guides/BOARD_BRINGUP.md)。

### Verified
- **版本链判定完成（这是本轮真正要的）**：用最小 Conv 模型走完 `pth→ONNX→RKNN(2.3.2)→板上推理`。
  板上回读到模型头 `toolkit version: 2.3.2, target platform: rk3576`，runtime 报
  `Model version: 2.3.2 not match with runtime 2.1.0`（**仅 warning**），`init_runtime ret=0`，
  `get_sdk_version()` = `API 2.1.0 (967d001cc8@2024-08-07) / DRV 0.9.8`，`inference()` 返回正常张量。
  ⇒ 转换端用 PyPI 能装到的 2.3.2，板端保持厂商 2.1.0，**不需要替系统库**。上一轮拟的 A/B 两条路线各对一半：A 预计要替库、B 预计要去取 2.1.0 老 wheel，两个都不必。
- `SFR_best.pth → sfrnet.onnx` 导出成功，torch-vs-onnx 相对误差 `6.95e-06`；`load_onnx` 返 0（570 节点全解析）。
- 开发机不需要 Docker：`rknn-toolkit2==2.3.2` 有 cp312 x86_64 wheel，且自带 torch 依赖（连带拉进 2.4.0）。

### Fixed
- **`build/README.md` 里写的 `python build/export_model.py onnx …` 本身就跑不通**：从仓库根启动时 `sys.path[0]` 是 `build/`，`app` 包导不进来。补 `sys.path.insert(仓库根)`；另外依赖链里 `PIL` 未声明。
- `export_model.py` 新增 `--no-quant`：出 fp16 产物，不需校准集。这不是为了方便，是**实验设计需要**——仓库里没有合规校准集（`load_calib_images` 自己写着“必须真实屏摄图，禁用合成图”，而现在只有 4 张非摄像头图），用 int8 做版本兼容性判定会直接违反 SDD §6 风险 #7。fp16 产物照样带 toolkit 版本头，兼容性检验效力不受影响（SDD §6 风险 #7 本来就把 fp16 列为合法回退）。
- `build/README.md` 第 ⑤ 条部署示例里**残留的第三处 `ssh firefly@`** 更正为 `industio@`（上一轮我只改了 scp 那一行，同一个错误在不同行重复出现）。

### Known Issues（新增与修正）
- **当前 SFRNet 转不出 .rknn**。不是版本问题，是 FRM 硬路由：
  `sfrnet.onnx` 在 `build()` 的 `fold_constant` 约 30% 处 **SIGABRT（exit 134）且无任何 Python 栈**；
  用单算子探针（`build/_probe_ops.py`）隔离出元凶：`Conv`/`ArgMax` 均能 build，`NonZero` 直接被拒
  （`The input 0 of NonZero need to be constant! It will cause the graph to be a dynamic graph!`）。
  来头：`upstream/sfr/FRM.py:88-99` 的 `(preds == i).nonzero()` + `if len(idx) > 0` + `output[idx] = …`。
- **比转不了更危险的第二层**：trace 会把路由决策钉死在 dummy 上。ONNX 直方图 `NonZero=4 / ScatterND=4 / Equal=4 / ArgMax=4 / Range=4` 刚好等于“4 个 block × 1 条分支”（FRM 实例仅 1 个），即其余 3 条专家分支根本没进图。实测**60 个真实切片共 240 次决策：`{专家1: 196, 专家3: 44}`，14/60 个切片的路由与 dummy 不同** ⇒ 当前 ONNX 对这些切片是语义错的。上面那条 6.95e-06 自检发现不了，因为自检用的就是同一个 dummy。
- **对本文已有条目的修正**：L395“上游 FRM Router 100% 坍缩到 branch_1”不够准确——整帧 512×512 输入（含 5 种内容）确实恒选专家 1，但细粒度切片下有 18% 走专家 3，且 softmax 接近均匀（top1-top2 差仅 0.018–0.054，四分类均匀为 0.25）。即“判别力不足 + 整帧层面近似恒定”，不是硬坍缩。已同步修正 SDD Q9。
- 上一条 L389（“`build/` 目录仍不存在，转换链未跑通；建议 Docker Ubuntu22.04 + py3.10”）**作废一半**：`build/` 早已存在，转换链也已实跑通到板端推理；且不需要 Docker / 不需要 py3.10（cp312 有 wheel）——但 cp312 换来两个自己的坑：`setuptools>=81` 删 `pkg_resources`、`onnx>=1.16` 删 `onnx.mapping` 且 **cp312 上 onnx≤1.15 无二进制 wheel**。

### 元教训：自检样本不能和被冻结的那个选择同源
上一轮记的是“实测值不等于可执行指令”（从 `librknnrt=2.1.0` 推“钉 toolkit2==2.1.0”）。
本轮是它的姊妹条：**“用 dummy 自己验 dummy”永远报绿**。torch-vs-ONNX 一致性只要复用导出时那个输入，
对任何“数据相关控制流被 trace 常量化”的模型都只能证明图结构没算错，**证明不了分支覆盖**。
另半条同样便宜：`RKNNLite` 没有 `.forward()` 只有 `.inference()`，我把接口名写错时差点把它当成版本不兼容的证据——失败现象要先归因到工具层还是模型层，再决定方不方便下结论。

---

## [1.0.0] - 2026-11-03

### 初始版本
- 基于 SFRNet 论文代码建立软件框架
- 完成 M0-M2 里程碑 (骨架 + 推理链 + 导出)
- 待完成: M3 (UI) / M4 (打包) / M5 (测试收尾)

---

*记录规范:*
- *每个版本按 Added / Changed / Deprecated / Removed / Fixed / Security 分类*
- *日期格式: YYYY-MM-DD*
- *重大变更需标注版本号*
