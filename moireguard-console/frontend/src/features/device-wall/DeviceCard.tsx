/** 单设备卡片：名称/serial/在线状态/engine_state/三态角标/elapsed/coverage/画面缩略 */
import { Box, Card, CardActionArea, CardContent, Chip, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';
import { mjpegUrl } from '@/api/client';
import { ConnBadge } from '@/components/ConnBadge';
import { StateBadge, WatermarkOverlay } from '@/components/StateBadge';

export function DeviceCard({ serial }: { serial: string }) {
  const info = useConsoleStore((s) => s.devices.find((d) => d.serial === serial) ?? null);
  const conn = useConsoleStore((s) => s.conn[serial] ?? 'connecting');
  const status = useConsoleStore((s) => s.status[serial] ?? null);
  const latest = useConsoleStore((s) => s.latest[serial] ?? null);
  const settings = useConsoleStore((s) => s.settings);
  const demoActive = useConsoleStore((s) => s.demoActive);

  const showStream = conn === 'online' && (info?.relay_enabled ?? false);
  const src = demoActive
    ? '' // Demo 模式无真实画面：占位灰块
    : mjpegUrl(settings.baseUrl || '', serial);

  return (
    <Card variant="outlined" sx={{ height: '100%' }}>
      <CardActionArea
        disableRipple
        component="a"
        href={`#/live/${encodeURIComponent(serial)}`}
        sx={{ height: '100%' }}
      >
        <Box sx={{ position: 'relative', bgcolor: '#263238', height: 160, overflow: 'hidden' }}>
          {showStream && src ? (
            <img
              src={src}
              alt={`实况 ${info?.name ?? serial}`}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={() => {
                // 画面（旁路能力，默认关）加载失败不崩溃：降级标记
                useConsoleStore.getState().markConn(serial, 'degraded');
              }}
            />
          ) : (
            <Box
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#90a4ae',
                fontSize: 13,
              }}
            >
              {info?.relay_enabled ? '画面未就绪' : '画面回传未开启（relay off）'}
            </Box>
          )}
          {/* 三态角标 + has_moire 不可信水印（画面与结果 frame_id 不保证一一对应） */}
          <Box sx={{ position: 'absolute', top: 6, right: 6 }}>
            <StateBadge result={latest?.result ?? null} compact />
          </Box>
          <WatermarkOverlay result={latest?.result ?? null} />
        </Box>
        <CardContent sx={{ pt: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography variant="subtitle1" fontWeight={700} noWrap>
              {info?.name ?? serial}
            </Typography>
            <ConnBadge conn={conn} />
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            serial: {serial}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Chip size="small" label={`engine: ${status?.engine_state ?? '-'}`} variant="outlined" />
            {latest && (
              <>
                <Chip size="small" label={`耗时 ${latest.elapsed_ms} ms`} variant="outlined" />
                <Chip
                  size="small"
                  label={`coverage ${(latest.coverage * 100).toFixed(1)}%`}
                  variant="outlined"
                />
              </>
            )}
          </Box>
          {latest && latest.warnings.length > 0 && (
            <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1 }}>
              ⚠ warnings: {latest.warnings.join(', ')}
            </Typography>
          )}
        </CardContent>
      </CardActionArea>
    </Card>
  );
}
