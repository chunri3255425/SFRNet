/**
 * 三态徽章（通用组件）。
 * 红线（docs/ARCHITECTURE.md §2.2 / §6 C3）：has_moire 判定恒真未解（板端
 * 风险 #12），任何 has_moire 展示必须带"⚠ 标定前数据 · 不可信"水印。
 */
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import type { ResultState } from '@/api/types';

const RESULT_META: Record<ResultState, { label: string; color: string; bg: string }> = {
  screen_detected: { label: '屏幕检测', color: '#1565c0', bg: '#e3f2fd' },
  has_moire: { label: '疑似摩尔纹', color: '#d32f2f', bg: '#ffebee' },
  no_moire: { label: '无摩尔纹', color: '#2e7d32', bg: '#e8f5e9' },
};

export const UNTRUSTED_WATERMARK = '⚠ 标定前数据 · 不可信';

export interface StateBadgeProps {
  result: ResultState | null;
  /** 是否以色块角标形态渲染（紧凑） */
  compact?: boolean;
}

export function StateBadge({ result, compact = false }: StateBadgeProps) {
  if (!result) {
    return (
      <Chip
        size="small"
        label="待判定"
        sx={{ backgroundColor: '#eceff1', color: '#607d8b' }}
      />
    );
  }
  const meta = RESULT_META[result];
  const chip = (
    <Chip
      size="small"
      label={compact ? meta.label : `${meta.label}`}
      sx={{ backgroundColor: meta.bg, color: meta.color, fontWeight: 700 }}
    />
  );
  if (result === 'has_moire') {
    return (
      <Tooltip title={UNTRUSTED_WATERMARK} arrow>
        <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
          {chip}
          {!compact && (
            <Typography variant="caption" sx={{ color: '#d32f2f', fontWeight: 700 }}>
              {UNTRUSTED_WATERMARK}
            </Typography>
          )}
        </Box>
      </Tooltip>
    );
  }
  return chip;
}

/** has_moire 时叠加在画面上的不可信水印层 */
export function WatermarkOverlay({ result }: { result: ResultState | null }) {
  if (result !== 'has_moire') return null;
  return (
    <Box
      data-testid="untrusted-watermark"
      sx={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        pt: 1,
        pointerEvents: 'none',
        bgcolor: 'rgba(211, 47, 47, 0.10)',
      }}
    >
      <Typography
        variant="subtitle2"
        sx={{
          color: '#d32f2f',
          fontWeight: 800,
          letterSpacing: 1,
          textShadow: '0 0 4px #fff',
          transform: 'rotate(-6deg)',
        }}
      >
        {UNTRUSTED_WATERMARK}
      </Typography>
    </Box>
  );
}
