/**
 * 全局类型定义 —— 字段名以 docs/ARCHITECTURE.md §2 为准（源自板端
 * DetectionResultDTO.summary()），不得凭空扩展。
 */

/** 三态判定结果（对外唯一承诺） */
export type ResultState = 'screen_detected' | 'has_moire' | 'no_moire';

/** 聚合 API：设备条目（GET /api/devices） */
export interface DeviceInfo {
  serial: string;
  name: string;
  host: string;
  http_port: number;
  relay_enabled: boolean;
}

/** 聚合 API：单设备状态（GET /api/devices/:serial/status） */
export interface DeviceStatus {
  engine_state: string;
  service_ready: boolean;
  sse_clients: number;
  uptime_s: number;
}

/** 聚合 API：SSE 事件（GET /api/events，data 载荷） */
export interface DetectionEvent {
  serial: string;
  result: ResultState;
  /** 超阈值像素占比（0~1 或 0~100 视板端口径，UI 仅做趋势展示） */
  coverage: number;
  /** 单帧端到端耗时 */
  elapsed_ms: number;
  /** 帧序号，追溯用（画面与结果不保证一一对应，UI 须容忍错位） */
  frame_id: number;
  /** 降级/警告标记（如级联禁用、DEGRADED） */
  warnings: string[];
  /** 仅级联开启时非空（屏幕置信度） */
  stage1_conf: number | null;
  /** 控制台收到时间（ms epoch） */
  ts: number;
}

/** 设备连接/在线状态（前端推断） */
export type ConnState = 'online' | 'offline' | 'degraded' | 'connecting';

/** 告警记录：连续 N 帧 has_moire 触发 */
export interface AlertRecord {
  id: string;
  serial: string;
  startedTs: number;
  count: number;
  frameIds: number[];
  maxCoverage: number;
}

/** 前端本地设置（localStorage 持久化） */
export interface Settings {
  baseUrl: string;
  demoMode: boolean;
  alertN: number;
}

/** 内部设备运行时视图（设备墙渲染用） */
export interface DeviceRuntime {
  info: DeviceInfo | null;
  status: DeviceStatus | null;
  conn: ConnState;
  latest: DetectionEvent | null;
}
