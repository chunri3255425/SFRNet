/** 实况视图：单设备放大页（画面 + 三态角标 + coverage 走势 + 判定流水） */
import { Box, Chip, Divider, Paper, Typography } from '@mui/material';
import { useParams } from 'react-router-dom';
import { useConsoleStore } from '@/store/store';
import { mjpegUrl } from '@/api/client';
import { ConnBadge } from '@/components/ConnBadge';
import { StateBadge, WatermarkOverlay } from '@/components/StateBadge';
import { Sparkline } from './Sparkline';
import { EventList } from '@/features/history/EventList';

export function LiveView() {
  const { serial = '' } = useParams<{ serial: string }>();
  const info = useConsoleStore((s) => s.devices.find((d) => d.serial === serial) ?? null);
  const conn = useConsoleStore((s) => s.conn[serial] ?? 'connecting');
  const status = useConsoleStore((s) => s.status[serial] ?? null);
  const latest = useConsoleStore((s) => s.latest[serial] ?? null);
  const series = useConsoleStore((s) => s.coverageSeries[serial] ?? []);
  const settings = useConsoleStore((s) => s.settings);
  const demoActive = useConsoleStore((s) => s.demoActive);

  const src = demoActive ? '' : mjpegUrl(settings.baseUrl || '', serial);

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="h6" fontWeight={700}>
          {info?.name ?? serial}
        </Typography>
        <ConnBadge conn={conn} />
        <Chip size="small" variant="outlined" label={`engine: ${status?.engine_state ?? '-'}`} />
        <Chip size="small" variant="outlined" label={`sse_clients: ${status?.sse_clients ?? '-'}`} />
        <Chip size="small" variant="outlined" label={`uptime: ${status?.uptime_s ?? '-'}s`} />
        {latest && <StateBadge result={latest.result} />}
        {latest && (
          <Chip size="small" variant="outlined" label={`frame_id: ${latest.frame_id}`} />
        )}
      </Box>

      <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
        <Paper variant="outlined" sx={{ flex: '1 1 480px', minWidth: 320, overflow: 'hidden' }}>
          <Box sx={{ position: 'relative', bgcolor: '#111', minHeight: 360 }}>
            {src ? (
              <img
                src={src}
                alt={`实况 ${info?.name ?? serial}`}
                style={{ width: '100%', minHeight: 360, objectFit: 'contain', display: 'block' }}
              />
            ) : (
              <Box
                sx={{
                  minHeight: 360,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#78909c',
                }}
              >
                {demoActive ? 'Demo 模式无真实画面' : '画面未就绪（relay 默认关闭 / 聚合服务未转发）'}
              </Box>
            )}
            <WatermarkOverlay result={latest?.result ?? null} />
          </Box>
        </Paper>

        <Box sx={{ flex: '1 1 320px', minWidth: 280 }}>
          <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              coverage 走势（最近 {series.length} 帧）
            </Typography>
            <Sparkline data={series} width={320} height={72} />
            {latest && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                最新 coverage {(latest.coverage * 100).toFixed(2)}% · 耗时 {latest.elapsed_ms} ms
                {latest.stage1_conf != null && ` · stage1_conf ${latest.stage1_conf}`}
              </Typography>
            )}
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              最近判定流水（本设备）
            </Typography>
            <Divider sx={{ mb: 1 }} />
            <EventList serial={serial} maxRows={12} />
          </Paper>
        </Box>
      </Box>
    </Box>
  );
}
