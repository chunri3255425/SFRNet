/** coverage 实时走势小图：SVG 手绘 sparkline（不引重型图表库） */
interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  /** 自动量程；否则按 0~1 */
  autoScale?: boolean;
}

export function Sparkline({ data, width = 320, height = 60, autoScale = false }: SparklineProps) {
  const n = data.length;
  if (n === 0) {
    return (
      <svg width={width} height={height} data-testid="sparkline">
        <rect width={width} height={height} fill="#eceff1" rx={4} />
        <text x={width / 2} y={height / 2 + 4} textAnchor="middle" fontSize={11} fill="#90a4ae">
          暂无 coverage 数据
        </text>
      </svg>
    );
  }
  const max = autoScale ? Math.max(0.01, ...data) : 1;
  const step = n > 1 ? width / (n - 1) : width;
  const points = data
    .map((v, i) => {
      const y = height - (Math.min(1, Math.max(0, v / max))) * (height - 6) - 3;
      return `${(i * step).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg width={width} height={height} data-testid="sparkline" role="img" aria-label="coverage 走势">
      <rect width={width} height={height} fill="#fafafa" rx={4} stroke="#e0e0e0" />
      <polyline
        points={points}
        fill="none"
        stroke="#1565c0"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      <text x={6} y={12} fontSize={10} fill="#78909c">
        {autoScale ? `max ${max.toFixed(2)}` : '0 ~ 1'}
      </text>
    </svg>
  );
}
