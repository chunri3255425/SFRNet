/**
 * Demo 模拟数据源：聚合 API 不可用时（或用户显式开启）使用。
 * 内置 3 台模拟设备，周期性产出 DetectionEvent（伪随机 coverage，
 * 偶发 has_moire 连片以便复现"连续 N 帧"告警），并伪造 DeviceStatus。
 */
import type {
  DetectionEvent,
  DeviceInfo,
  DeviceStatus,
  ResultState,
} from './types';

export const MOCK_DEVICES: DeviceInfo[] = [
  { serial: '98fed70982ab4c63', name: '实验室-1号板', host: 'moireguard-98fed70982ab4c63.local', http_port: 8080, relay_enabled: true },
  { serial: 'a1b2c3d4e5f60718', name: '产线-2号板', host: '192.168.1.42', http_port: 8080, relay_enabled: true },
  { serial: 'f00dc0ffee000123', name: '展台-3号板', host: 'moireguard-f00dc0ffee000123.local', http_port: 8080, relay_enabled: false },
];

export function mockDeviceStatus(serial: string): DeviceStatus {
  const seed = serial.length;
  return {
    engine_state: 'running',
    service_ready: true,
    sse_clients: 1 + (seed % 3),
    uptime_s: 3600 + seed * 137,
  };
}

/** 伪随机数（种子稳定，便于演示复现） */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(0x4d6f6965); // "Moie"

/** 每台设备独立的"连片 has_moire"状态：偶尔进入 moire 段（连续若干帧），复现告警 */
interface MoireRun {
  remaining: number;
}

const runs: Record<string, MoireRun> = {};
for (const d of MOCK_DEVICES) runs[d.serial] = { remaining: 0 };

export function mockNextEvent(serial: string, now: number): DetectionEvent {
  const run = runs[serial] ?? { remaining: 0 };
  let result: ResultState;
  let coverage: number;
  if (run.remaining > 0) {
    // moire 连片段：coverage 偏高且抖动
    run.remaining -= 1;
    result = rand() < 0.85 ? 'has_moire' : 'screen_detected';
    coverage = 0.25 + rand() * 0.35;
  } else {
    const r = rand();
    if (r < 0.06) {
      // 开启新的 moire 连片段（长度 4~8 帧，便于触发连续 N 帧告警）
      run.remaining = 4 + Math.floor(rand() * 5);
      result = 'has_moire';
      coverage = 0.3 + rand() * 0.3;
    } else if (r < 0.3) {
      result = 'screen_detected';
      coverage = 0.02 + rand() * 0.08;
    } else {
      result = 'no_moire';
      coverage = 0.005 + rand() * 0.02;
    }
  }
  runs[serial] = run;
  const warnings: string[] = [];
  if (rand() < 0.05) warnings.push('DEGRADED');
  return {
    serial,
    result,
    coverage: Math.round(coverage * 10000) / 10000,
    elapsed_ms: Math.round(120 + rand() * 180),
    frame_id: Math.floor(now / 500) + Math.floor(rand() * 3),
    warnings,
    stage1_conf: Math.round((0.9 + rand() * 0.09) * 1000) / 1000,
    ts: now,
  };
}

/**
 * Demo 事件源句柄：interval 定时对每台在线设备各产出一帧事件。
 * 行为对齐 client.openEventStream 的 handle（onEvent / close）。
 */
export interface MockHandle {
  close: () => void;
}

export function startMockFeed(
  onEvent: (e: DetectionEvent) => void,
  periodMs = 900,
): MockHandle {
  const timer = setInterval(() => {
    const now = Date.now();
    for (const d of MOCK_DEVICES) {
      // 3 号板偶发离线，演示"离线/降级"态
      if (d.serial === 'f00dc0ffee000123' && Math.floor(now / 20000) % 2 === 0) continue;
      onEvent(mockNextEvent(d.serial, now));
    }
  }, periodMs);
  return { close: () => clearInterval(timer) };
}
