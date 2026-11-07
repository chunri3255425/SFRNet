# MoiréGuard 镜像构建流水线（build/）

> 交付品是**可烧录镜像**（SDD §1.2）。本目录负责其中「模型转换」与「运行包组装」两个环节。

## 全流程一览

```
SFR_best.pth ──①export_model.py onnx──> sfrnet_static.onnx（默认开 FRM 静态化）
                      │
                      ├─② export_model.py rknn（内部先自动跑 onnxslim 常量折叠）──> sfrnet.rknn（int8，需 rknn-toolkit2 @ x86 Linux）
                      │        └──一致性比对（误差 >5% → 降位宽/fp16，SDD §6 风险 #7）
                      ▼
app/ 源码 ──③package_app.py──> build/dist/moireguard-board.tar.gz
                              （/opt /etc /usr 分层 stage，含 systemd 单元）
                              │
                              ├──④ 镜像集成：Purple Pi OH2 Linux 根文件系统叠加
                              │     （buildroot/yocto recipe 或 dd 烧录后 SSH 解压）
                              └──④ OH 镜像集成（image_ohos/，待 OH 方案确认）
                              ▼
                     可烧录镜像（.img）→ 烧录 → 上电即跑 → SSH 运维 / WiFi 看画面
```

## 目录

| 路径 | 内容 |
|---|---|
| `export_model.py` | pth→ONNX→RKNN 转换 + 量化一致性比对 |
| `package_app.py` | 板端运行包打包（免依赖安装，落盘路径见脚本内 `DEST`） |
| `image_linux/` | Linux 侧集成产物（systemd 服务单元等） |
| `image_ohos/` | OpenHarmony 侧集成（占位，待 OH 镜像方案确认） |
| `calib/` | RKNN 量化校准集：**100–200 张真实屏摄图**（可从 UHDMD 抽样；禁用合成图，见 SDD §6 风险 #7） |
| `_probe_bisect.py` / `_probe_shapes.py` / `_probe_ops.py` | 转换失败取证三件套：前缀二分定位静默 SIGABRT / 符号形状普查 / 单算子隔离 |
| `_probe_eval_x86.py` → `_probe_board_run.py` | 跨设备对照：x86 备输入与 onnxruntime 参照，板端加载 .rknn 跑两种布局 |
| `_probe_threshold.py` | 判定层阈值扫描（prob 分位数 + 可分性），实测结论见 [BOARD_BRINGUP §15.4](../docs/guides/BOARD_BRINGUP.md) |
| `check_docs.py` | 文档机械自检：围栏成对 / 表格列数 / §交叉引用落点 / 错字。改完文档必跑 |
| `models/` | 转换产物（.onnx / .rknn，不入库，见 .gitignore） |
| `dist/` | 打包产物（不入库） |

## 环境分工（不可装反）

| 环境 | 装什么 | 干什么 |
|---|---|---|
| x86 开发机（Linux/WSL/Docker） | `rknn-toolkit2==2.3.2`（连带拉进 torch 2.4.0 / onnxruntime / opencv / numpy 1.26，**不需另装 torch**） | 训练权重转换、量化、一致性比对。✅ **版本链已实测判定**（[BOARD_BRINGUP §13.1](../docs/guides/BOARD_BRINGUP.md)）：转换端用 PyPI 能装到的 2.3.2，产物能被板上厂商 **2.1.0** `librknnrt.so` 加载并推理成功（仅报一行 version-not-match warning），**不需要替系统库** |
| 板端（Purple Pi OH2, RK3576） | `rknn-toolkit-lite2`（**实测走 PyPI pip 即可**：有 cp310 manylinux aarch64 wheel，本次已装成 2.3.2） | NPU 推理；**板上无 PyTorch**，但需额外 apt 装 `python3-numpy` / `python3-opencv`（官方镜像不带） |
| 开发 PC（SSH 客户端） | — | `ssh`/`scp` 运维；上位机画面查看器（TCP 9000 收 JPEG） |

## 快速命令

```bash
# ① pth → onnx（开发机；默认对 FRM 硬路由做导出侧静态化，产物才转得动 RKNN）
python build/export_model.py onnx --weights assets/weights/SFR_best.pth --out build/models/sfrnet_static.onnx

# ② onnx → rknn int8（x86 Linux + rknn-toolkit2）——需 build/calib/ 里有真实屏摄图
python build/export_model.py rknn --onnx build/models/sfrnet_static.onnx --out build/models/sfrnet.rknn --calib build/calib/

# ②' fp16 不量化：不需校准集。**已实测跑通的就是这条**（约 1 m 47 s，产物 50.9 MB，板端 NPU 与 onnxruntime 相关系数 1.0000）
python build/export_model.py rknn --onnx build/models/sfrnet_static.onnx --out build/models/sfrnet_fp16.rknn --no-quant

# ③ 打板端运行包
python build/package_app.py --model build/models/sfrnet.rknn

# ④ 烧录/部署（板上已刷官方 Linux 镜像时，SSH 直装即可验收）
#    用户名/主机名已板端实测：官方 Ubuntu 固件为 `industio`（旧文档里的 firefly 不对）
scp build/dist/moireguard-board.tar.gz industio@<板IP>:~/
ssh industio@<板IP> 'sudo tar xzf ~/moireguard-board.tar.gz -C / && \
                    sudo systemctl enable --now moireguard'

# ⑤ 远程观察（可选，旁路）
ssh industio@<板IP> 'sudo systemctl stop moireguard && \
                    cd /opt/moireguard && sudo python3 -m app.main --relay --relay-host <PC_IP> --frames 0'
# PC 端：python build/tools/view_relay.py --listen 9000  （JPEG 帧播放 + has_moire 角标）

# ⑥ 板上跑产品代码路径（验收形态； adb 直推源码即可，不必打包）
#    ⚠ stdout 只出 rknn 自己的日志，应用日志在板上 /var/log/moireguard/moireguard.log
A=Tools/ADB工具/adb_win/adb.exe
$A push app /root/app && $A push configs /root/configs && $A push samples/moire_photo.jpg /root/
$A shell 'cd /root && python3 -m app.main --backend rknn --model /root/sfrnet_wired_fp16.rknn \
          --camera /root/moire_photo.jpg --frames 2; cat /var/log/moireguard/moireguard.log'
```

## 转换环境复现（2026-11-06 / 11-07 实测踩过的四个坑，都是真坑）

```bash
/usr/bin/python3.12 -m venv .venv-convert
.venv-convert/bin/pip install "rknn-toolkit2==2.3.2" pillow onnxslim
.venv-convert/bin/pip install "setuptools<81"        # 坑 1：>=81 删了 pkg_resources，RKNN() 直接起不来
```

| 坑 | 现象 | 处置 |
|---|---|---|
| 1 `setuptools>=81` | `No module named 'pkg_resources'` | 钉 `setuptools<81` |
| 2 `onnx.mapping` | `module 'onnx' has no attribute 'mapping'`；onnx 1.16 删了它，而 **cp312 上 onnx≤1.15 无二进制 wheel**（pip 退回源码构建并失败） | 给转换 venv 挨个补 shim：`site-packages/onnx/mapping.py` + `onnx/__init__.py` 末尾挂 `import onnx.mapping`。仅适用于一次性 venv，**不是产品代码** |
| 3 依赖声明不全 | `No module named 'app'` → `No module named 'PIL'` | `export_model.py` 已补 `sys.path.insert(仓库根)`；需 `pillow` |
| 4 **导出产物形状不静态** | `build()` 直接 **SIGABRT（无算子名、无 Python 栈）**；实为 944/1324 张量含符号维度 | 必须常量折叠：`pip install onnxslim`（已实测 0.1.99），`rknn` 子命令已默认自动跑；可 `--no-simplify` 关闭取证。取证工具：`build/_probe_bisect.py`（前缀二分）、`build/_probe_shapes.py`（符号形状普查） |

另：板端推理接口叫 **`RKNNLite.inference()`**，没有 `.forward()`；别把接口名写错当成版本不兼容。

> ✅ **链已打通（2026-11-07 实测，[BOARD_BRINGUP §14](../docs/guides/BOARD_BRINGUP.md)）**：pth → 静态化 ONNX → 常量折叠 → fp16 `.rknn` → 板端 NPU 输出与 onnxruntime 参照相关系数 1.0000。之前写的“当前 SFRNet 转不到 .rknn”已推翻（NonZero 只是其中一个障碍，主因是坑 4）。
>
> ⚠ **预处理口径**：`rknn.config` 烘的是**恒等** mean/std（`MEAN_BAKED/STD_BAKED`），因为 rknn 的 mean/std 直接作用于喂入张量、**不会帮你 /255**。归一化仍在调用侧（契约 C1），板端要喂 **NHWC float32 归一化**张量（实测 NCHW 喂法相关系数只有 0.03，且不报错）。
>
> ⚠ **延迟实测（2026-11-07 产品代码路径，[BOARD_BRINGUP §15](../docs/guides/BOARD_BRINGUP.md)）**：**单块** 512×512 fp16 ≈ 3.0 s；**整帧** 2275×1279（32 块）端到端 **96.6 s**，4K（112 块）外推约 340 s。别把两个数混着说——上一轮我就是这么把延迟低估了一个数量级。
>
> ⚠ **判定层未标定**：现配置（threshold 0.5 / coverage_threshold 0.01）下**干净屏摄截图也报有摩尔纹**（coverage 0.98–0.999，与摩尔纹图 0.997 无法区分）。转换链已排除嫌疑（板端与 x86 torch 同图 coverage 差 4×10⁻⁶）；`_probe_threshold.py` 只能给出窗口，标定要等真实负样本。
>
> ⚠ **int8 仍未转过**：缺 100–200 张真实屏摄校准集（SDD §6 风险 #7）。校准集现在会先过一遍 `load_calib_images` 落成归一化 `.npy` 再交给 toolkit，保证校准域 == 推理域。
>
> 另另：① 里“torch-vs-onnx 自检通过”**不能当成导出正确的证据**——它只验证了 dummy 那一次的路由选择，必须另做多样本路由抽检（`build/_probe_routing.py`）。

## 验收口径（SDD §1.2）

空白板 → 烧录镜像 → 接 IMX415 → 上电 → 无任何手动配置 → 自动开始检测并输出布尔结果；
SSH 可登录运维；`--relay` 打开后上位机可实时看到画面与判定角标。
