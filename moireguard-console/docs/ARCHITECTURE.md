# MoireGuard Console — 前端架构与对接契约

> **状态：`[规划]`，代码零落地。** 本文件是上位机前端（多设备管理 / 视频接收 / 异常展示）的**设计正源**。
> 板端能力以 `SFRNet/docs/design/SOFTWARE_DESIGN.md` 为准，本文只**引用**不**复制**；两处冲突以板端文档 + 板端实测为准。
> 与板端关系：控制台是**消费侧 / 聚合侧**，SFRNet 是**数据生产侧**；依赖方向单向（控制台 → 板端），板端不感知控制台存在。

---

## 1. 定位与边界

| 维度 | 板端 SFRNet（已实现） | 上位机控制台（本文，规划） |
|---|---|---|
| 角色 | 数据生产者（检测 + 出帧） | 数据消费者 + 多设备编排 |
| 是否必需联网 | 否（断网照常检测，板端 §4.5 R7） | 是（存在意义即集中观察） |
| 交付位置 | `SFRNet/`，烧进 .img | `SFRNet/` **仓库内子目录** `moireguard-console/`，**不进镜像** |
| 对外协议 | HTTP/SSE（结果）+ TCP JPEG（画面） | 消费上述协议；对浏览器另暴露自己的 REST/WS |

**铁律（延续板端 §4.5"旁路诊断面"纪律）**：控制台断开，板端照常检测；控制台**绝不反向阻塞**检测循环。所有对板端的调用都必须是"读多写少、可失败、不回填压"。

---

## 2. 板端可用接口（对接正源，实测于 `SFRNet/app/adaptation/network/http_server.py`）

控制台的一切能力都建立在这四个端点 + 一条画面回传通路之上。**本文所有字段名以该源码为准，不得凭空扩展。**

### 2.1 HTTP 控制/结果面（单台板）

| 端点 | 方法 | 鉴权 | 用途 | 响应 |
|---|---|---|---|---|
| `/healthz` | GET | **免 token** | 存活探针（systemd/监控） | `{"ok": true}` |
| `/status` | GET | Bearer | 引擎与适配层快照（只读） | `{engine_state, service_ready, sse_clients, uptime_s, adaptation?}` |
| `/stream` | GET | Bearer | **SSE**：每产生一次判定推一行 | `data: {DetectionResultDTO.summary()}\n\n`，`retry: 3000`，`: keepalive` 心跳 |
| `/detect` | POST | Bearer | 上传单图判定 | `{status:"ok", ...dto.summary()}`；`X-With-Mask:1` 时附 `mask_png_b64` |

- **鉴权口径**：`network.token` 非空时要求 `Authorization: Bearer <token>`；空 token 视为"仅限开发机"，`/status` 启动会打 WARN（源码 108-109 行）。`/healthz` 唯一免鉴权，其余一律过 token。
- **`/detect` 的形态限制**：常驻循环形态（`--serve-net --loop`）**故意不提供 `/detect`**（返回 503 `E-NO-UPLOAD`），逐帧判定改由 `/stream` 推送；上传检图需用 `--serve-net` 单独启动形态。→ 控制台对"常驻板"只订阅 `/stream`，对"检图板"才调 `/detect`。
- **SSE 是主控通道**：多设备实时状态与三态结果都应从 `/stream` 取，`/status` 用于快照/健康轮询，`/detect` 仅按需单图。

### 2.2 结果对象 `DetectionResultDTO.summary()` 字段（三态语义源）

引用板端 §4.3.8 / §4.4，`summary()` 至少含：

```
result ∈ {"screen_detected", "has_moire", "no_moire"}   # 对外唯一承诺（三态）
coverage        # 超阈值像素占比，诊断/趋势用
elapsed_ms      # 单帧端到端耗时
frame_id        # 帧序号，追溯用
warnings        # 降级/警告标记（如级联禁用、DEGRADED）
stage1_conf     # 仅级联开启非空（屏幕置信度）
```

> ⚠ 板端 §6 风险 #12 未解：当前 `has_moire` 对干净图也恒为 True，**控制台展示层必须带"标定前/未验收"水印**，不得把原始 `has_moire` 当交付结论直接呈现（见 §6 风险 C3）。

### 2.3 画面回传通路（TCP JPEG）

引用板端 §4.5.2 `IFrameRelay`：板端把帧 **JPEG 编码后经 TCP 长连接推送**，自定义 **4 字节长度前缀分包**。板端侧已实施的关键约束（控制台必须顺应、不可要求板端违反）：

- **R1 丢旧保新**：待发槽位恒 1，新帧覆盖旧帧 → 控制台收到的是"当前画面"，**不得要求补发历史帧**。
- **R3 降帧率**：默认 `interval_frames` 抽帧（如每 6 帧 1 帧 ≈ 5fps），非全帧率。
- **R6 降分辨率**：回传前可下采样（如长边 1080），与板端检测用原图解耦。
- **R7 默认关闭**：`relay.enabled` 镜像默认 false，需现场/SSH 打开——**画面能力不是板上电即有，属可失败旁路**。

> 画面端口/协议细节（监听 host:port、分包大小端）属板端 `wifi_frame_relay.py` 实现，**F-A 阶段需读源码冻结为契约**（见 §5）。

---

## 3. 三大能力设计

### 3.1 多设备管理（`server/`）

- **注册与发现**：沿用板端 §4.5.1 —— mDNS（`moireguard-<serial>.local`）与静态 IP 两种登记。控制台的设备清单落在 `configs/devices.yaml`（见 §4）。
- **在线判定**：对每台板轮询 `/healthz`（免鉴权、成本最低）判存活；`/status` 取 `engine_state`/`service_ready`/`sse_clients`/`uptime_s`。
- **实时结果**：对每台"常驻板"维持一条 `/stream` SSE 长连接，事件进入控制台侧统一事件总线（板端 SSE 慢消费者会被丢弃——控制台侧须自己保证消费足够快或做本地缓冲，**不可要求板端为重发**）。
- **配置下发**：把板端 §4.3.6 热更新参数（`threshold` / `coverage_threshold` / `cascade.enabled` / `cascade.screen_conf` / `cascade.gray_zone_policy`）写回目标板覆盖文件。⚠ 该通路目前走 **SSH/scp 控制面**（板端无"写配置"HTTP 端点），非数据面，失败不影响板端检测。

### 3.2 视频传输（接收侧，`server/` → `frontend/`）

```
板端 IFrameRelay (TCP JPEG)
   → server/  relay 接收器（每板一连接，4 字节前缀拆帧）
   → 转码/直接转发到浏览器（MJPEG over HTTP 或 WebSocket 二进制帧）
   → frontend/ 实况画面（最新帧，带 result 角标）
```

- **多路并发**：N 台板 × 低帧率；带宽/解码压力全在控制台侧，板端只出一份降帧率画面。
- **不回压板端**：接收器落后时**丢帧**（与板端 R1 同构），绝不反向施加背压。
- **画面与结果对齐**：画面角标用该帧对应 `frame_id` 的最近 `result`；因 R1 丢帧，画面与 SSE 结果**不保证逐帧一一对应**，UI 需容忍轻微错位。

### 3.3 异常判别展示（`frontend/`）

- **设备墙**：每台板一卡，显示在线状态、`engine_state`、最新三态（色块：screen/moire/no-moire）、最近 `elapsed_ms`、画面缩略。
- **实况视图**：单放大画面 + 三态角标 + coverage 走势小图。
- **告警策略**：应用侧跨帧一致性（如"连续 N 帧 `has_moire` 才告警"）——板端 §4.3.4 已明确 V1 **不做**跨帧平滑，此策略天然落控制台。
- **历史与导出**：判定流水、`coverage` 趋势持久化，供板端 Q5/Q13 阈值标定回看——**控制台成为标定数据的采集端**，导出格式与板端标定脚本对齐。

---

## 4. 目标目录结构

```
SFRNet\                           # 板端仓库（GitHub: chunri3255425/SFRNet）
├── app\ ...                      # 板端主体（与前端不互相 import）
└── moireguard-console\           # ★ 上位机前端（仓库内子目录，不进镜像）
    ├── README.md               # 定位、与 SFRNet 关系、当前状态（规划中）
    ├── docs\
    │   └── ARCHITECTURE.md     # 本文件：对接契约、页面、协议
    ├── server\                 # 聚合 / 转发 / 设备管理后端（规划，F-B）
    ├── frontend\               # Web UI（规划，F-C）
    ├── configs\
    │   ├── devices.example.yaml  # 设备清单模板（入库）：serial/host/token 引用名
    │   └── (tokens/、*.local.yaml 被 .gitignore 排除)
    └── .gitignore
```

- `server/`、`frontend/`、`configs/` 现为占位目录（`.gitkeep`），随 §5 路线填充。
- **技术栈独立选型**，不与板端 Python 绑定；首版建议：`frontend/` Web（浏览器集中看多路），`server/` 一个轻量聚合服务（Node 或 Python 均可，F-A 定）。
- **凭据红线**（沿用板端 §4.5.1）：`token`/设备凭据**绝不入库**，`configs/devices.example.yaml` 只存引用名，真实值走被 `.gitignore` 排除的 `configs/tokens/` 或环境变量。

`configs/devices.example.yaml` 草案（F-A 冻结）：

```yaml
devices:
  - serial: "98fed70982ab4c63"
    name: "实验室-1号板"
    host: "moireguard-98fed70982ab4c63.local"   # mDNS，或静态 IP
    http_port: 8080
    token_ref: "lab1"            # 指向 configs/tokens/lab1（不入库）
    relay: { enabled: false }    # 画面回传默认关，与板端 R7 对齐
```

---

## 5. 实施路线

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **F-A 契约冻结** | 读 `wifi_frame_relay.py` 锁定 relay 分包协议；把 §2 端点/字段与 `devices.yaml` schema 定稿写入本文件；定 `server/` 技术栈 | 单机连**一台**板：`/healthz` 通、`/stream` 能收三态、能收到至少 1 帧画面 |
| **F-B 聚合服务** | `server/`：设备注册/发现、多路 `/stream` 收敛、relay 接收与转发、`/status` 轮询、（可选）配置下发经 SSH | **≥3 台板并发**且**不回压板端检测**（板端 `elapsed_ms` 不因接入控制台而上升） |
| **F-C Web 前端** | 设备墙 + 实况画面 + 三态角标 + "连续 N 帧"告警 + 历史/coverage 走势 | 浏览器一屏看多设备，画面最新帧、角标正确、告警可复现 |
| **F-D 配置与标定** | 控制台回写热更新参数；导出判定流水/coverage 供板端 Q5/Q13 联合标定 | 标定数据可复用回板端设计文档耗时/阈值表 |

> **纪律**：接入后所有"控制台使板端变慢/丢帧"的怀疑，都要用板端 `elapsed_ms`/`sse_clients` 前后实测对比坐实——这与板端"数字必须实测禁推算"的纪律一致。

---

## 6. 风险与待决

| # | 事项 | 说明 | 归属 |
|---|---|---|---|
| C1 | 板端网络出口**尚未正式立项**（板端 §4.4 待 Q4） | 控制台强依赖它；先坐实为稳定契约再建前端，否则多设备管理无接口可接 | 前置，F-A 前确认 |
| C2 | 多路 4K/降帧率 relay 聚合带宽 | 板端已 R3/R6 减负；控制台侧仍需评估 N 台并发上限与转发带宽 | F-B |
| C3 | **判定恒真（板端 §6 风险 #12）未解** | `has_moire` 当前不可信，展示层须带"标定前"水印，避免误导决策 | F-C 展示 + 依赖板端 Q5 |
| C4 | 设备鉴权与凭据管理 | token 不入本仓库（板端 §4.5.1），`configs/tokens/` 被 .gitignore 排除，走独立配置源 | F-A |
| C5 | 配置下发只有 SSH 一条路 | 板端无"写配置"HTTP 端点，控制台需 SSH 凭据与主机密钥管理；失败仅影响下发不影响板端 | F-B |
| C6 | 画面与结果帧错位 | relay R1 丢帧 + SSE 独立通道，`frame_id` 对齐是尽力而为 | F-C UI 容忍 |

---

## 7. 与板端文档的引用映射（避免复制漂移）

| 本文概念 | 板端权威出处 |
|---|---|
| 三态结果语义 | `SOFTWARE_DESIGN.md` §4.3.8 / §4.4 |
| SSE/REST 端点与鉴权 | `SFRNet/app/adaptation/network/http_server.py`（实测） |
| 画面回传协议与 R1–R7 | §4.5.2 `IFrameRelay` + `adaptation/relay/wifi_frame_relay.py` |
| 热更新参数 | §4.3.6 配置热更新 |
| 设备发现（mDNS/SSH 控制面） | §4.5.1 SSH 运维通道 |
| 判定恒真（不可信 has_moire） | §6 风险 #12 |
| 跨帧不平滑（告警落应用侧） | §4.3.4 判定规则 |
