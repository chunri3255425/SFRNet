/** 设备墙：一屏多设备卡片 */
import { Alert, Box, CircularProgress, Grid, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';
import { DeviceCard } from './DeviceCard';

export function DeviceWall() {
  const devices = useConsoleStore((s) => s.devices);
  const demoActive = useConsoleStore((s) => s.demoActive);

  if (devices.length === 0) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 4 }}>
        <CircularProgress size={24} />
        <Typography>正在获取设备清单…（若聚合服务未启动，将自动降级 Demo 模式）</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      {demoActive && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Demo 模拟数据模式：聚合服务不可达或已显式开启，当前展示为内置模拟设备与模拟事件流。
        </Alert>
      )}
      <Grid container spacing={2}>
        {devices.map((d) => (
          <Grid item xs={12} sm={6} md={4} key={d.serial}>
            <DeviceCard serial={d.serial} />
          </Grid>
        ))}
      </Grid>
    </Box>
  );
}
