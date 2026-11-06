# API 文档

本目录存放 MoiréGuard 的 API 参考文档。

---

## 网络出口（代码已落地，非占位）

实现在 `app/adaptation/network/http_server.py`，由 `python -m app.main --serve-net` 启动；
配置见 `configs/default.yaml` 的 `network` 节（默认 `enabled: false`）。
下列 4 个端点与鉴权行为已在开发机实测（`tests/test_real_weights.py` 第 4 组）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针，不触发推理；**免鉴权**（探针不持 token，误加鉴权会把健康服务判成死亡） |
| GET | `/status` | 引擎状态、配置快照 |
| GET | `/stream` | SSE 事件流（逐帧结果心跳推送） |
| POST | `/detect` | 上传图像字节（`Content-Type: image/jpeg`）或 JSON 引用本地图（需 `allow_paths`），返回判定 |

- 鉴权：`network.token` 非空时要求 `Authorization: Bearer <token>`，缺失返回 **401**
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
