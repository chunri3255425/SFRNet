/**
 * QA 单元测试：连续 N 帧 has_moire 告警判定（纯函数）
 * 覆盖：不足 N 不告警 / 恰好 N 告警 / 夹杂非 has_moire 后重新计数 / 跨设备隔离 / 超过 N 不重复告警
 */
import { describe, it, expect } from 'vitest';
import { INITIAL_STREAK, isAlertActive, stepStreak } from '@/features/alerts/consecutive';
import type { DetectionEvent } from '@/api/types';

function ev(overrides: Partial<DetectionEvent> = {}): DetectionEvent {
  return {
    serial: 'DEV-A',
    result: 'has_moire',
    coverage: 0.3,
    elapsed_ms: 100,
    frame_id: 1,
    warnings: [],
    stage1_conf: null,
    ts: 1000,
    ...overrides,
  };
}

describe('stepStreak - 连续 N 帧判定', () => {
  it('不足 N 帧 has_moire 不触发告警', () => {
    let s = INITIAL_STREAK;
    for (let i = 1; i < 5; i++) {
      const r = stepStreak(s, ev({ frame_id: i, ts: 1000 + i }), 5);
      s = r.state;
      expect(r.firedAlert).toBeNull();
    }
    expect(s.count).toBe(4);
  });

  it('恰好第 N 帧 has_moire 触发告警，且记录正确的起始时间与帧号', () => {
    let s = INITIAL_STREAK;
    let alert = null;
    for (let i = 1; i <= 3; i++) {
      const r = stepStreak(s, ev({ frame_id: i, ts: 100 * i, coverage: 0.1 * i }), 3);
      s = r.state;
      if (r.firedAlert) alert = r.firedAlert;
    }
    expect(alert).not.toBeNull();
    expect(alert!.count).toBe(3);
    expect(alert!.startedTs).toBe(100); // 第一帧的时间戳
    expect(alert!.frameIds).toEqual([1, 2, 3]);
    expect(alert!.maxCoverage).toBeCloseTo(0.3);
    expect(alert!.serial).toBe('DEV-A');
  });

  it('超过 N 帧后不重复触发告警（只在恰好跨过阈值时触发一次）', () => {
    let s = INITIAL_STREAK;
    const fired: unknown[] = [];
    for (let i = 1; i <= 5; i++) {
      const r = stepStreak(s, ev({ frame_id: i }), 3);
      s = r.state;
      if (r.firedAlert) fired.push(r.firedAlert);
    }
    expect(fired.length).toBe(1);
    expect(isAlertActive(s, 3)).toBe(true); // streak 仍进行中
  });

  it('夹杂 no_moire 后连续段清零，重新计数', () => {
    let s = INITIAL_STREAK;
    // 2 帧 has_moire
    s = stepStreak(s, ev({ frame_id: 1 }), 3).state;
    s = stepStreak(s, ev({ frame_id: 2 }), 3).state;
    // 打断帧
    const brk = stepStreak(s, ev({ frame_id: 3, result: 'no_moire' }), 3);
    expect(brk.firedAlert).toBeNull();
    expect(brk.state.count).toBe(0);
    expect(brk.state.frameIds).toEqual([]);
    // screen_detected 同样应打断
    const brk2 = stepStreak(brk.state, ev({ frame_id: 4, result: 'screen_detected' }), 3);
    expect(brk2.state.count).toBe(0);
    // 重新计 2 帧仍不告警
    s = stepStreak(brk2.state, ev({ frame_id: 5 }), 3).state;
    s = stepStreak(s, ev({ frame_id: 6 }), 3).state;
    expect(s.count).toBe(2);
    const r = stepStreak(s, ev({ frame_id: 7 }), 3);
    expect(r.firedAlert).not.toBeNull();
    expect(r.firedAlert!.frameIds).toEqual([5, 6, 7]);
    expect(r.firedAlert!.startedTs).toBe(ev({ frame_id: 5 }).ts);
  });

  it('跨设备 serial 隔离：不同设备状态互不影响', () => {
    const sA = stepStreak(INITIAL_STREAK, ev({ serial: 'DEV-A', frame_id: 1 }), 2).state;
    const sB = stepStreak(INITIAL_STREAK, ev({ serial: 'DEV-B', frame_id: 1 }), 2).state;
    // DEV-A 再来一帧达到阈值；DEV-B 不受影响
    const rA = stepStreak(sA, ev({ serial: 'DEV-A', frame_id: 2 }), 2);
    expect(rA.firedAlert).not.toBeNull();
    expect(rA.firedAlert!.serial).toBe('DEV-A');
    // DEV-B 保持独立计数
    expect(sB.count).toBe(1);
    expect(isAlertActive(sB, 2)).toBe(false);
    const rB = stepStreak(sB, ev({ serial: 'DEV-B', frame_id: 2, result: 'no_moire' }), 2);
    expect(rB.firedAlert).toBeNull();
    expect(rB.state.count).toBe(0);
  });

  it('n <= 0 时永不告警（防御性）', () => {
    let s = INITIAL_STREAK;
    for (let i = 1; i <= 10; i++) {
      const r = stepStreak(s, ev({ frame_id: i }), 0);
      s = r.state;
      expect(r.firedAlert).toBeNull();
    }
    expect(s.count).toBe(10);
  });

  it('frameIds 环形缓冲截断到 64（防内存膨胀）', () => {
    let s = INITIAL_STREAK;
    for (let i = 1; i <= 100; i++) {
      s = stepStreak(s, ev({ frame_id: i }), 3).state;
    }
    expect(s.frameIds.length).toBeLessThanOrEqual(64);
    expect(s.frameIds[s.frameIds.length - 1]).toBe(100);
  });
});

describe('isAlertActive', () => {
  it('streak >= n 视为进行中', () => {
    expect(isAlertActive({ ...INITIAL_STREAK, count: 3 }, 3)).toBe(true);
    expect(isAlertActive({ ...INITIAL_STREAK, count: 2 }, 3)).toBe(false);
    expect(isAlertActive(INITIAL_STREAK, 1)).toBe(false);
  });
});
