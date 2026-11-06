# API 文档

本目录存放 MoiréGuard 的 API 参考文档。

---

## 网络出口（代码已落地，非占位）

实现在 `app/adaptation/network/http_server.py`，有两种启动形态：`--serve-net`
只开网络面（上传检图，主机侧常用）；`--serve-net --loop` 同时跑常驻检测循环（板端形态，
判定由 `/stream` 实时推，此形态不提供 `/detect`）；
配置见 `configs/default.yaml` 的 `network` 节（默认 `enabled: false`）。
下列 4 个端点与鉴权/排空行为均已实测：无权重时用 `tests/smoke_board_pipeline.py` 第 8 组，
真权重下用 `tests/test_real_weights.py` 第 4 组。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针，不触发推理；**免鉴权**（探针不持 token，误加鉴权会把健康服务判成死亡） |
| GET | `/status` | 引擎状态、配置快照。`--loop` 形态下 `engine_state` 来自真实状态机（`BOOTING/READY/DETECTING/DEGRADED/SHUTDOWN`）；此时 `service_ready` 恒为 `false`，含义是「本形态不接上传检测」，不是故障 |
| GET | `/stream` | SSE 事件流。`--loop` 形态下推的是**常驻循环的逐帧判定**；纯 `--serve-net` 形态下只有经 `/detect` 进来的检测才会出现在这里 |
| POST | `/detect` | 上传图像字节（`Content-Type: image/jpeg`）或 JSON 引用本地图（需 `allow_paths`），返回判定。**`--loop` 形态返回 503 `E-NO-UPLOAD`**（不为此多加载一份权重） |

- 鉴权：`network.token` 非空时要求 `Authorization: Bearer <token>`，缺失返回 **401**
- 错误响应的体量规则（客户端行为有依赖，写清）：
  - 客户端带 `Expect: 100-continue`（curl 对 >1024B 体默认带）时，鉴权不过就在传体前
    直接回 401 并关连接，**上传字节为 0**（实测）；鉴权过则正常回 `100 Continue`，若不
    回这一行，curl 会白等一秒才开始传体（实测 1.008 s vs 0.048 s）。
  - 不带 `Expect` 的大 body，服务端先排空再回状态码，排空上限 = `network.max_upload_mb`；
    超过上限的声明体量不排空、直接断连（不为一个发送方读无界数据）。
  - 因此 1 MB / 4 MB 的未授权上传都能真拿到 401，而不是写入途中撞 broken pipe；同一条
    keep-alive 连接在 401 / 503 之后仍可继续用（`smoke_board_pipeline` 第 8 组钉住）。
- 代理环境注意：开发机上 `no_proxy` 里的 `127.*` 写法 Python 不识别（
  `proxy_bypass("127.0.0.1")` 返 `False`），回环请求会先绕系统代理一跳，代理把上游中断
  翻译成的 502 会被当成服务端行为。自用脚本请显式直连（`ProxyHandler({})` / `curl --noproxy '*'`）
- 掩膜：默认**不**返回；带请求头 `X-With-Mask: 1` 才附 `mask_png_b64`（省带宽）
- 判定字段：`has_moire` `coverage` `mean_prob` `max_prob` `tiles` `elapsed_ms` `warnings`

---

## 待补充

- [ ] 适配层 API（`IRuntimeAdapter` / `ICameraAdapter` / `IDeviceIO` / `IFrameRelay` 与反向注入的 Protocol）

- [ ] 模型层 API
  - `IInputAdapter` 接口说明
  - `IOutputAdapter` 接口说明
  - `DetectorCore` 协议说明
  - `ModelRegistry` 使用说明

- [ ] 逻辑层 API
  - `InferenceService` 使用说明
  - `SlidingWindowEngine` 参数说明
  - `PostProcessor` 配置说明
  - `ExportService` 导出格式说明

- [ ] 配置说明
  - `default.yaml` 完整配置项
  - `models.yaml` 模型注册格式

---

*当前版本: v1.0.0 (占位)*
