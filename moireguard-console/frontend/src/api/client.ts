/**
 * 聚合 API 客户端：fetch 封装 + SSE 封装。
 *
 * 约定（见 frontend/README.md "API 契约"节）：
 *  - GET  /api/devices                    → DeviceInfo[]
 *  - GET  /api/devices/:serial/status     → DeviceStatus
 *  - GET  /api/events                     → SSE，data 为 DetectionEvent
 *  - GET  /api/devices/:serial/stream     → MJPEG multipart/x-mixed-replace
 *
 * 所有失败（网络错误 / HTTP 4xx-5xx / JSON 解析失败）一律 throw ApiError，
 * 由上层统一降级（标记离线或切 Demo 模式），UI 不崩溃。
 */
import type { DeviceInfo, DeviceStatus, DetectionEvent, ResultState } from './types';

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

function url(base: string, path: string): string {
  const b = base.replace(/\/+$/, '');
  return `${b}${path}`;
}

async function getJson<T>(base: string, path: string, timeoutMs = 5000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url(base, path), { signal: controller.signal });
    if (!res.ok) {
      throw new ApiError(`GET ${path} -> HTTP ${res.status}`, res.status);
    }
    const data = (await res.json()) as T;
    return data;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(`GET ${path} failed: ${(err as Error).message}`);
  } finally {
    clearTimeout(timer);
  }
}

/** 拉取设备清单 */
export function fetchDevices(base: string): Promise<DeviceInfo[]> {
  return getJson<DeviceInfo[]>(base, '/api/devices');
}

/** 拉取单设备状态快照 */
export function fetchDeviceStatus(base: string, serial: string): Promise<DeviceStatus> {
  return getJson<DeviceStatus>(base, `/api/devices/${encodeURIComponent(serial)}/status`);
}

/** 单设备实况画面（MJPEG）地址，直接塞 <img src> */
export function mjpegUrl(base: string, serial: string): string {
  return url(base, `/api/devices/${encodeURIComponent(serial)}/stream`);
}

export interface EventStreamHandle {
  close: () => void;
}

/** 把事件时间统一成毫秒 epoch；无效值退回本地当前时间。 */
export function normalizeTs(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return Date.now();
  return n < 1e11 ? n * 1000 : n;
}

/**
 * 订阅全局 SSE 事件流 /api/events。
 * EventSource 自带断线重连；onError 只上报（连接断开时标记离线），
 * 不自行销毁 —— 由调用方决定是否切 Demo。
 */
export function openEventStream(
  base: string,
  onEvent: (e: DetectionEvent) => void,
  onError: (err: Error) => void,
): EventStreamHandle {
  let es: EventSource | null = null;
  let closed = false;
  try {
    es = new EventSource(url(base, '/api/events'));
  } catch (err) {
    onError(new ApiError(`EventSource 创建失败: ${(err as Error).message}`));
    return { close: () => { closed = true; } };
  }
  es.onmessage = (msg: MessageEvent<string>) => {
    try {
      const data = JSON.parse(msg.data) as DetectionEvent;
      // 基本字段防御：result 必须是三态之一，否则丢弃该事件（容忍错位/脏数据）
      const valid: ResultState[] = ['screen_detected', 'has_moire', 'no_moire'];
      if (!valid.includes(data.result)) return;
      onEvent({
        ...data,
        warnings: Array.isArray(data.warnings) ? data.warnings : [],
        coverage: Number(data.coverage) || 0,
        elapsed_ms: Number(data.elapsed_ms) || 0,
        frame_id: Number(data.frame_id) || 0,
        // ts 单位归一：板端 DTO 的 ts 是 epoch 秒（base.py 字段说明 +
        // engine.py 用 time.time()），而 EventList / csv / AlertBanner 全都按
        // new Date(ts) 当毫秒用。不换算的话相邻两帧差 19 秒会被读成 19 毫秒，
        // 整屏事件挤进同一秒显示。阈值 1e11 区分秒/毫秒（毫秒级 epoch 到
        // 公元 5138 年才越过它，秒级则早已越过）。
        ts: normalizeTs(data.ts),
      });
    } catch {
      // 解析失败：忽略该帧，UI 不崩溃
    }
  };
  es.onerror = () => {
    if (!closed) onError(new ApiError('SSE 连接错误（EventSource 将自动重连）'));
  };
  return {
    close: () => {
      closed = true;
      es?.close();
    },
  };
}
