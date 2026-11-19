# MoiréGuard Console — Web 前端（F-C）

上位机控制台前端：多设备墙 / 实况视图 / 连续 N 帧告警 / 判定流水与 CSV 导出。
架构正源见 `../docs/ARCHITECTURE.md`；与板端 `app/` 代码零依赖（不互相 import）。

## 运行方式

```bash
# Node 18+（Node 22 已验证路径：C:\Users\Lenovo\.workbuddy\binaries\node\versions\22.12.0\node.exe）
npm install --prefix .                # 网络失败可加 --registry=https://registry.npmmirror.com
npm run dev                           # http://localhost:5173（/api 已代理到 http://localhost:8000）
npx tsc --noEmit                      # 类型检查
npm run build                         # 类型检查 + 产物构建（dist/）
```

> 若 `npm install` 失败，代码仍完整且类型自洽（`npx tsc --noEmit` 通过），
> 按上方命令安装依赖后即可运行。

## 降级策略

- 设置页可配置聚合服务 Base URL（localStorage），留空走同源 + Vite 代理。
- 聚合服务不可达（fetch 失败 / SSE error / HTTP 4xx-5xx / 设备清单为空）→ **自动降级 Demo 模拟数据模式**：内置 3 台模拟设备 + 模拟 SSE 事件流（含偶发 has_moire 连片，可复现告警），UI 不崩溃。
- 也可在设置页强制开启 Demo 模式。
- 单台设备 `/status` 轮询失败只标记该台"离线"，整体不降级。

## API 契约（与 `server/` 聚合服务约定，F-B 实现时按此对齐）

| 端点 | 方法 | 响应 |
|---|---|---|
| `/api/devices` | GET | `[{serial, name, host, http_port, relay_enabled}]` |
| `/api/devices/:serial/status` | GET | `{engine_state, service_ready, sse_clients, uptime_s}` |
| `/api/events` | GET (SSE) | `data: {serial, result, coverage, elapsed_ms, frame_id, warnings, stage1_conf, ts}` |
| `/api/devices/:serial/stream` | GET | MJPEG `multipart/x-mixed-replace`（直接塞 `<img src>`） |

字段语义沿用板端 `DetectionResultDTO.summary()`（三态 `result ∈ screen_detected / has_moire / no_moire`），见 `../docs/ARCHITECTURE.md` §2。所有请求失败统一进入降级（标记离线或切 Demo），UI 容忍画面与结果 `frame_id` 错位（relay R1 丢帧）。

## 功能与红线

- **设备墙**：卡片含名称/serial/在线状态/engine_state/三态色块角标/最近 elapsed_ms/coverage/画面缩略。
- **实况视图**：MJPEG 画面 + 三态角标 + coverage SVG sparkline 走势 + 最近判定流水。
- **告警策略**：应用侧"连续 N 帧 `has_moire` 才告警"（板端 V1 不做跨帧平滑）；N 可调（默认 3）；纯函数实现见 `src/features/alerts/consecutive.ts`（可单测）；告警横幅 + 告警历史页。
- **历史与导出**：判定流水存内存 + localStorage（上限 2000 条），可导出 CSV（列：timestamp, serial, result, coverage, elapsed_ms, frame_id, warnings）。
- **合规红线（板端 §6 风险 #12，has_moire 判定恒真未解）**：所有 `has_moire` 展示（徽章、横幅、告警历史、画面水印）一律带 **"⚠ 标定前数据 · 不可信"**。

## 目录结构

```
src/
├── main.tsx / App.tsx / index.css
├── api/          types.ts（类型契约）/ client.ts（fetch+SSE 封装）/ mock.ts（Demo 数据源）
├── store/        store.ts（zustand）/ useBridge.ts（数据源接线与降级）
├── features/
│   ├── device-wall/  DeviceWall.tsx / DeviceCard.tsx
│   ├── live-view/    LiveView.tsx / Sparkline.tsx
│   ├── alerts/       consecutive.ts（纯函数）/ AlertBanner.tsx / AlertsPage.tsx
│   ├── history/      csv.ts / EventList.tsx / HistoryPage.tsx
│   └── settings/     SettingsPage.tsx
└── components/   StateBadge.tsx（三态徽章+水印）/ ConnBadge.tsx
```

技术栈：Vite + React 18 + TypeScript + MUI v5（组件为主）+ Tailwind CSS（布局微调）+ zustand（轻量状态）。
