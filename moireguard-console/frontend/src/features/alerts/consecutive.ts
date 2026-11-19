/**
 * 告警策略：连续 N 帧 has_moire 才告警（应用侧跨帧一致性，
 * 板端 §4.3.4 明确 V1 不做跨帧平滑 —— 本策略天然落控制台）。
 *
 * ★ 纯函数，无副作用，便于单元测试。
 */
import type { AlertRecord, DetectionEvent } from '@/api/types';

/** 单设备连续帧计数状态 */
export interface StreakState {
  count: number;
  /** 连续段起始时间戳 */
  startedTs: number;
  frameIds: number[];
  maxCoverage: number;
}

export const INITIAL_STREAK: StreakState = {
  count: 0,
  startedTs: 0,
  frameIds: [],
  maxCoverage: 0,
};

/**
 * 推进一步连续帧计数。
 * @param prev  该设备当前 StreakState
 * @param event 最新判定事件
 * @param n     告警阈值（连续 N 帧 has_moire）
 * @returns 新的 StreakState；若恰好跨过阈值且之前未达标，附带一条新告警记录
 */
export function stepStreak(
  prev: StreakState,
  event: DetectionEvent,
  n: number,
): { state: StreakState; firedAlert: AlertRecord | null } {
  if (event.result === 'has_moire') {
    const state: StreakState = {
      count: prev.count + 1,
      startedTs: prev.count === 0 ? event.ts : prev.startedTs,
      frameIds: [...prev.frameIds, event.frame_id].slice(-64),
      maxCoverage: Math.max(prev.maxCoverage, event.coverage),
    };
    if (state.count === n && n > 0) {
      return {
        state,
        firedAlert: {
          id: `${event.serial}-${event.ts}`,
          serial: event.serial,
          startedTs: state.startedTs,
          count: state.count,
          frameIds: [...state.frameIds],
          maxCoverage: state.maxCoverage,
        },
      };
    }
    return { state, firedAlert: null };
  }
  // 非 has_moire：连续段清零
  return { state: { ...INITIAL_STREAK }, firedAlert: null };
}

/**
 * 判定某条告警是否"仍在进行"（用于横幅常驻展示）：
 * 事件流里该设备最近一条事件距告警触发不超过 staleMs 且告警段未被打断。
 * 简化实现：只要该设备当前 streak 仍 ≥ n 就视为进行中。
 */
export function isAlertActive(streak: StreakState, n: number): boolean {
  return streak.count >= n;
}
