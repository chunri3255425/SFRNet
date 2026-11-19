/**
 * QA 单元测试：判定流水 CSV 导出（RFC 4180 转义）
 */
import { describe, it, expect } from 'vitest';
import { eventsToCsv } from '@/features/history/csv';
import type { DetectionEvent } from '@/api/types';

function ev(overrides: Partial<DetectionEvent> = {}): DetectionEvent {
  return {
    serial: 'DEV-A',
    result: 'no_moire',
    coverage: 0.01,
    elapsed_ms: 120,
    frame_id: 42,
    warnings: [],
    stage1_conf: null,
    ts: Date.UTC(2026, 0, 2, 3, 4, 5),
    ...overrides,
  };
}

describe('eventsToCsv', () => {
  it('基础行包含表头与全部字段', () => {
    const csv = eventsToCsv([ev()]);
    const lines = csv.trimEnd().split('\r\n');
    expect(lines[0]).toBe('timestamp,serial,result,coverage,elapsed_ms,frame_id,warnings');
    expect(lines[1]).toContain('DEV-A');
    expect(lines[1]).toContain('no_moire');
    expect(lines[1]).toContain('42');
    expect(lines[1]).toContain('2026-01-02T03:04:05'); // ISO 时间戳
    expect(csv.endsWith('\r\n')).toBe(true);
  });

  it('warnings 含逗号 → 引号包裹且双引号转义', () => {
    const csv = eventsToCsv([ev({ warnings: ['a,b"c'] })]);
    // 逗号/引号必须不出现在裸字段中：该字段应为 "a,b""c"
    expect(csv).toContain('"a,b""c"');
  });

  it('多行 warnings 通过 join 合并，不破坏行结构', () => {
    const csv = eventsToCsv([ev({ warnings: ['DEGRADED', 'CASCADE'] })]);
    expect(csv.trimEnd().split('\r\n').length).toBe(2); // 表头 + 1 数据行
    expect(csv).toContain('DEGRADED|CASCADE');
  });

  it('多事件输出多行，顺序保持', () => {
    const csv = eventsToCsv([ev({ frame_id: 1 }), ev({ frame_id: 2 })]);
    const lines = csv.trimEnd().split('\r\n');
    expect(lines.length).toBe(3);
    expect(lines[1].indexOf('42')).toBeLessThan(lines[1].indexOf('DEV-A'));
  });

  it('空列表仅返回表头', () => {
    const csv = eventsToCsv([]);
    expect(csv.trimEnd()).toBe('timestamp,serial,result,coverage,elapsed_ms,frame_id,warnings');
  });
});
