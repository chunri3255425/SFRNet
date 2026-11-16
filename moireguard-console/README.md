# MoireGuard Console — 上位机前端（多设备管理 / 视频接收 / 异常展示）

> **状态：规划中，代码零落地。** 本目录是摩尔纹视觉识别系统**上位机侧**的产物根，
> 位于 `SFRNet/` **仓库内的 `moireguard-console/` 子目录**，与板端 `app/` 代码相互独立（不互相 import）。**不进板端烧录镜像**（`build/` 打包不纳入本目录）。
> 前端设计以本目录 `docs/ARCHITECTURE.md` 为准；板端能力引用 `SFRNet/docs/design/SOFTWARE_DESIGN.md`（§4.4 网络出口、§4.5 旁路、§4.3 逻辑层契约）。

## 1. 它是什么

SFRNet 是**板端数据面**：上电自启、断网也能完成检测并输出三态
（`screen_detected` / `has_moire` / `no_moire`）。本控制台是**消费侧 / 聚合侧**，
通过板端已有的两条网络出口集中观察与编排**多台**开发板：

- 结果出口：HTTP/SSE（`SFRNet` `adaptation/network/http_server.py`）
- 画面出口：WiFi 帧回传 `IFrameRelay`（TCP + JPEG 帧序列，4 字节长度前缀分包）

**铁律（延续板端 §4.5 旁路诊断面纪律）**：控制台断开，板端照常检测；控制台绝不反向阻塞检测循环。

## 2. 三大能力（规划）

| 能力 | 说明 |
|---|---|
| 多设备管理 | 设备注册/发现（mDNS `moireguard-<serial>.local` 或静态 IP）、在线状态、健康聚合、热更新参数下发（走 SSH/scp 控制面） |
| 视频传输 | 消费板端 relay 帧序列并转发到浏览器；遵循丢旧保新、降帧率，只展示"当前画面" |
| 异常判别展示 | 三态渲染 + 角标、跨帧告警（"连续 N 帧 has_moire 才告警"）、判定流水与 coverage 趋势导出（供阈值标定回看） |

## 3. 目录约定

```
moireguard-console/
├── README.md               # 本文件
├── docs/
│   └── ARCHITECTURE.md     # 对接契约、页面、协议细化
├── server/                 # 聚合 / 转发 / 设备管理后端（规划，F-B）
├── frontend/               # Web UI（规划，F-C）
├── configs/                # 控制台自身配置：设备清单、token 等（不入库敏感项）
└── .gitignore
```

`server/`、`frontend/`、`configs/` 目前为占位（含 `.gitkeep`），随实施路线 F-A→F-D 逐步填充。

## 4. 实施路线（详见 `docs/ARCHITECTURE.md`）

| 阶段 | 内容 | 验收 |
|---|---|---|
| F-A 契约冻结 | 写死板端 SSE/REST 与 relay 帧字段（三态、coverage、frame_id、serial）到 `docs/ARCHITECTURE.md` | 单机连一台板同时收到结果 + 画面 |
| F-B 聚合服务 | `server/` 设备注册、多路 relay 收敛、状态轮询 | ≥3 台板并发且不回压板端检测 |
| F-C Web 前端 | 设备墙 + 实况画面 + 三态角标 + 告警 | 浏览器一屏看多设备 |
| F-D 配置与标定 | 回写热更新参数、导出判定流水供 Q5/Q13 | 标定数据可复用回设计文档 |

## 5. 前置依赖与已知约束（务必先读）

- ⚠ 板端网络出口目前"代码先行、是否升正式通道待 Q4"（见设计文档 §4.4）。**控制台立项前需先坐实该出口为稳定契约**，否则多设备管理无接口可接。
- ⚠ 判定恒真（设计文档 §6 风险 #12）未解：当前 `has_moire` 不可信，展示层须带"标定前"水印，避免误导。
- 鉴权 token 不入本仓库（沿用板端 §4.5.1 约束），走 `configs/` 独立配置源且被 `.gitignore` 排除。
