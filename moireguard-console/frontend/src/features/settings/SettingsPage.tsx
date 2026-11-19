/** 设置页：聚合服务 base URL / Demo 开关 / 告警 N 值 */
import { useState } from 'react';
import {
  Box,
  Button,
  FormControlLabel,
  Paper,
  Slider,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import { useConsoleStore, DEFAULT_SETTINGS } from '@/store/store';

export function SettingsPage() {
  const settings = useConsoleStore((s) => s.settings);
  const setSettings = useConsoleStore((s) => s.setSettings);
  const reset = useConsoleStore((s) => s.reset);
  const [baseUrl, setBaseUrl] = useState(settings.baseUrl);

  return (
    <Box sx={{ p: 3, maxWidth: 640 }}>
      <Typography variant="h6" gutterBottom>
        设置（localStorage 持久化，保存后立即生效并重连）
      </Typography>
      <Paper variant="outlined" sx={{ p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <TextField
          label="聚合服务 Base URL"
          placeholder="例如 http://192.168.1.10:8000（留空则用同源 + Vite 代理 /api）"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          onBlur={() => setSettings({ baseUrl: baseUrl.trim() })}
          size="small"
          helperText="GET /api/devices、/api/events(SSE)、/api/devices/:serial/stream 均基于此地址"
        />
        <FormControlLabel
          control={
            <Switch
              checked={settings.demoMode}
              onChange={(e) => setSettings({ demoMode: e.target.checked })}
            />
          }
          label="强制 Demo 模拟数据模式（关闭时聚合服务不可达会自动降级为 Demo）"
        />
        <Box>
          <Typography gutterBottom>
            告警阈值 N：连续 <b>{settings.alertN}</b> 帧 has_moire 才告警
          </Typography>
          <Slider
            min={1}
            max={10}
            step={1}
            marks
            value={settings.alertN}
            valueLabelDisplay="auto"
            onChange={(_, v) => setSettings({ alertN: v as number })}
          />
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="contained"
            onClick={() => {
              setSettings({ baseUrl: baseUrl.trim() });
              reset();
            }}
          >
            保存并重连
          </Button>
          <Button
            variant="text"
            onClick={() => {
              setBaseUrl(DEFAULT_SETTINGS.baseUrl);
              setSettings({ ...DEFAULT_SETTINGS });
              reset();
            }}
          >
            恢复默认
          </Button>
        </Box>
      </Paper>
    </Box>
  );
}
