/** 告警历史页：已触发的"连续 N 帧 has_moire"记录 */
import { Box, List, ListItem, Paper, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';

export function AlertsPage() {
  const alerts = useConsoleStore((s) => s.alerts);
  const devices = useConsoleStore((s) => s.devices);
  const nameOf = (serial: string) => devices.find((d) => d.serial === serial)?.name ?? serial;

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom>
        告警历史（连续 N 帧 has_moire，{alerts.length} 条）
      </Typography>
      <Paper variant="outlined" sx={{ p: 2 }}>
        {alerts.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            暂无告警记录
          </Typography>
        ) : (
          <List dense>
            {alerts
              .slice()
              .reverse()
              .map((a) => (
                <ListItem key={a.id} disableGutters>
                  <Box>
                    <Typography variant="body2">
                      {new Date(a.startedTs).toLocaleString()} — {nameOf(a.serial)}：连续 {a.count} 帧
                      has_moire（max coverage {(a.maxCoverage * 100).toFixed(1)}%）
                    </Typography>
                    <Typography variant="caption" color="warning.main">
                      标定前数据 · 不可信
                    </Typography>
                  </Box>
                </ListItem>
              ))}
          </List>
        )}
      </Paper>
    </Box>
  );
}
