/**
 * 全局状态：zustand。设备清单 / 连接态 / 最新事件 / 判定流水 / 告警 /
 * coverage 走势 / 本地设置（localStorage 持久化）。
 */
import { create } from 'zustand';
import type {
  AlertRecord,
  ConnState,
  DetectionEvent,
  DeviceInfo,
  DeviceStatus,
  Settings,
} from '@/api/types';
import { stepStreak, type StreakState } from '@/features/alerts/consecutive';

const SETTINGS_KEY = 'moireguard-console-settings';
const HISTORY_KEY = 'moireguard-console-history';
const HISTORY_CAP = 2000; // localStorage 上限：超出丢弃最旧（简化持久化）
const SERIES_CAP = 120; // coverage sparkline 窗口

export const DEFAULT_SETTINGS: Settings = {
  baseUrl: '',
  demoMode: false, // false = 自动探测（失败自动降级 Demo）
  alertN: 3,
};

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : DEFAULT_SETTINGS.baseUrl,
      demoMode: Boolean(parsed.demoMode),
      alertN: Number(parsed.alertN) > 0 ? Number(parsed.alertN) : DEFAULT_SETTINGS.alertN,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

interface ConsoleState {
  settings: Settings;
  demoActive: boolean; // 实际生效：true 表示当前数据源为 Demo
  devices: DeviceInfo[];
  conn: Record<string, ConnState>;
  status: Record<string, DeviceStatus>;
  latest: Record<string, DetectionEvent>;
  streak: Record<string, StreakState>;
  coverageSeries: Record<string, number[]>;
  history: DetectionEvent[];
  alerts: AlertRecord[];

  setSettings: (s: Partial<Settings>) => void;
  setDemoActive: (v: boolean) => void;
  ingestDevices: (devices: DeviceInfo[]) => void;
  ingestStatus: (serial: string, status: DeviceStatus) => void;
  markConn: (serial: string, conn: ConnState) => void;
  ingestEvent: (e: DetectionEvent) => void;
  clearHistory: () => void;
  reset: () => void;
}

function persistHistory(history: DetectionEvent[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_CAP)));
  } catch {
    // 存储满/不可用：忽略，内存态仍在
  }
}

export const useConsoleStore = create<ConsoleState>((set) => ({
  settings: loadSettings(),
  demoActive: false,
  devices: [],
  conn: {},
  status: {},
  latest: {},
  streak: {},
  coverageSeries: {},
  history: (() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      return raw ? (JSON.parse(raw) as DetectionEvent[]) : [];
    } catch {
      return [];
    }
  })(),
  alerts: [],

  setSettings: (s) =>
    set((st) => {
      const next = { ...st.settings, ...s };
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return { settings: next };
    }),

  setDemoActive: (v) => set({ demoActive: v }),

  ingestDevices: (devices) =>
    set((st) => {
      const conn: Record<string, ConnState> = { ...st.conn };
      for (const d of devices) {
        if (!conn[d.serial]) conn[d.serial] = 'connecting';
      }
      return { devices, conn };
    }),

  ingestStatus: (serial, status) =>
    set((st) => ({
      status: { ...st.status, [serial]: status },
      // /status 成功至少说明聚合服务可达：connecting → online
      conn: st.conn[serial] === 'connecting' ? { ...st.conn, [serial]: 'online' } : st.conn,
    })),

  markConn: (serial, conn) => set((st) => ({ conn: { ...st.conn, [serial]: conn } })),

  ingestEvent: (e) =>
    set((st) => {
      const prevStreak = st.streak[e.serial] ?? { count: 0, startedTs: 0, frameIds: [], maxCoverage: 0 };
      const { state: nextStreak, firedAlert } = stepStreak(prevStreak, e, st.settings.alertN);
      const series = (st.coverageSeries[e.serial] ?? []).concat(e.coverage).slice(-SERIES_CAP);
      const history = st.history.concat(e);
      if (history.length > HISTORY_CAP) {
        history.splice(0, history.length - HISTORY_CAP);
      }
      persistHistory(history);
      return {
        latest: { ...st.latest, [e.serial]: e },
        streak: { ...st.streak, [e.serial]: nextStreak },
        coverageSeries: { ...st.coverageSeries, [e.serial]: series },
        history,
        alerts: firedAlert ? st.alerts.concat(firedAlert).slice(-200) : st.alerts,
        conn: st.conn[e.serial] === 'offline' || st.conn[e.serial] === 'degraded'
          ? { ...st.conn, [e.serial]: 'online' }
          : st.conn,
      };
    }),

  clearHistory: () => {
    persistHistory([]);
    set({ history: [], alerts: [], streak: {}, coverageSeries: {}, latest: {} });
  },

  reset: () =>
    set({
      devices: [],
      conn: {},
      status: {},
      latest: {},
      streak: {},
      coverageSeries: {},
      alerts: [],
    }),
}));
