# MoiréGuard 镜像构建流水线（build/）

> 交付品是**可烧录镜像**（SDD §1.2）。本目录负责其中「模型转换」与「运行包组装」两个环节。

## 全流程一览

```
SFR_best.pth ──①export_model.py onnx──> sfrnet.onnx
                      │
                      ├─②export_model.py rknn──> sfrnet.rknn（int8，需 rknn-toolkit2 @ x86 Linux）
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
| `models/` | 转换产物（.onnx / .rknn，不入库，见 .gitignore） |
| `dist/` | 打包产物（不入库） |

## 环境分工（不可装反）

| 环境 | 装什么 | 干什么 |
|---|---|---|
| x86 开发机（Linux/WSL/Docker） | PyTorch + `rknn-toolkit2` | 训练权重转换、量化、一致性比对 |
| 板端（Purple Pi OH2, RK3576） | `rknn-toolkit-lite2`（厂商 .deb） | NPU 推理；**板上无 PyTorch** |
| 开发 PC（SSH 客户端） | — | `ssh`/`scp` 运维；上位机画面查看器（TCP 9000 收 JPEG） |

## 快速命令

```bash
# ① pth → onnx（开发机）
python build/export_model.py onnx --weights assets/weights/SFR_best.pth --out build/models/sfrnet.onnx

# ② onnx → rknn int8（x86 Linux + rknn-toolkit2）
python build/export_model.py rknn --onnx build/models/sfrnet.onnx --out build/models/sfrnet.rknn --calib build/calib/

# ③ 打板端运行包
python build/package_app.py --model build/models/sfrnet.rknn

# ④ 烧录/部署（板上已刷官方 Linux 镜像时，SSH 直装即可验收）
scp build/dist/moireguard-board.tar.gz firefly@<板IP>:~/
ssh firefly@<板IP> 'sudo tar xzf ~/moireguard-board.tar.gz -C / && \
                    sudo systemctl enable --now moireguard'

# ⑤ 远程观察（可选，旁路）
ssh firefly@<板IP> 'sudo systemctl stop moireguard && \
                    cd /opt/moireguard && sudo python3 -m app.main --relay --relay-host <PC_IP> --frames 0'
# PC 端：python build/tools/view_relay.py --listen 9000  （JPEG 帧播放 + has_moire 角标）
```

## 验收口径（SDD §1.2）

空白板 → 烧录镜像 → 接 IMX415 → 上电 → 无任何手动配置 → 自动开始检测并输出布尔结果；
SSH 可登录运维；`--relay` 打开后上位机可实时看到画面与判定角标。
