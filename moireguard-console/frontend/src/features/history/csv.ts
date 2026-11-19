/**
 * 判定流水 CSV 导出。
 * 列：timestamp, serial, result, coverage, elapsed_ms, frame_id, warnings
 * （与 docs/ARCHITECTURE.md §2.2 字段对齐，供板端 Q5/Q13 标定回看）
 */
import type { DetectionEvent } from '@/api/types';

const CSV_HEADER = 'timestamp,serial,result,coverage,elapsed_ms,frame_id,warnings';

function esc(v: string): string {
  return `"${v.replace(/"/g, '""')}"`;
}

/** 生成 CSV 文本（RFC 4180 风格引号转义） */
export function eventsToCsv(events: DetectionEvent[]): string {
  const lines = [CSV_HEADER];
  for (const e of events) {
    lines.push(
      [
        new Date(e.ts).toISOString(),
        e.serial,
        e.result,
        String(e.coverage),
        String(e.elapsed_ms),
        String(e.frame_id),
        esc((e.warnings ?? []).join('|')),
      ].join(','),
    );
  }
  return lines.join('\r\n') + '\r\n';
}

/** 触发浏览器下载 */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function exportHistoryCsv(events: DetectionEvent[]): void {
  if (events.length === 0) return;
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  downloadCsv(`moireguard-events-${ts}.csv`, eventsToCsv(events));
}
