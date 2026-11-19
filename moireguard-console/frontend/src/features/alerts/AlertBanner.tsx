/** 告警横幅：显示"仍在进行中"的连续 N 帧 has_moire 告警 */
import { Alert, Box, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';
import { isAlertActive } from './consecutive';

export function AlertBanner() {
  const devices = useConsoleStore((s) => s.devices);
  const streaks = useConsoleStore((s) => s.streak);
  const alerts = useConsoleStore((s) => s.alerts);
  const alertN = useConsoleStore((s) => s.settings.alertN);

  const active = alerts.filter(
    (a) => isAlertActive(streaks[a.serial] ?? { count: 0, startedTs: 0, frameIds: [], maxCoverage: 0 }, alertN),
  );
  if (active.length === 0) return null;

  const nameOf = (serial: string) =>
    devices.find((d) => d.serial === serial)?.name ?? serial;

  return (
    <Box sx={{ px: 3, pt: 2 }}>
      {active.map((a) => (
        <Alert
          key={a.id}
          severity="error"
          variant="filled"
          sx={{ mb: 1 }}
          data-testid="alert-banner"
        >
          <Box>
            <Typography fontWeight={700}>
              ⚠ 告警：{nameOf(a.serial)} 连续 {a.count} 帧 has_moire
            </Typography>
            <Typography variant="caption" sx={{ display: 'block' }}>
              自 {new Date(a.startedTs).toLocaleTimeString()} 起 · 最大 coverage {(a.maxCoverage * 100).toFixed(1)}%
              · frames {a.frameIds.slice(-6).join(',')}
            </Typography>
            <Typography variant="caption" sx={{ display: 'block', fontWeight: 700 }}>
              （该判定为标定前数据 · 不可信，仅供诊断参考）
            </Typography>
          </Box>
        </Alert>
      ))}
    </Box>
  );
}
