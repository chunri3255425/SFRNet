/** 连接状态徽章（在线/离线/降级/连接中） */
import { Chip } from '@mui/material';
import type { ConnState } from '@/api/types';

const META: Record<ConnState, { label: string; color: 'success' | 'error' | 'warning' | 'default' }> = {
  online: { label: '在线', color: 'success' },
  offline: { label: '离线', color: 'error' },
  degraded: { label: '降级', color: 'warning' },
  connecting: { label: '连接中', color: 'default' },
};

export function ConnBadge({ conn }: { conn: ConnState }) {
  const meta = META[conn];
  return <Chip size="small" color={meta.color} variant="outlined" label={meta.label} />;
}
