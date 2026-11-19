/** 判定流水列表（通用，可按 serial 过滤） */
import { Box, List, ListItem, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';
import { StateBadge } from '@/components/StateBadge';

export function EventList({ serial, maxRows = 20 }: { serial?: string; maxRows?: number }) {
  const history = useConsoleStore((s) => s.history);
  const rows = (serial ? history.filter((e) => e.serial === serial) : history)
    .slice()
    .reverse()
    .slice(0, maxRows);

  if (rows.length === 0) {
    return <Typography variant="body2" color="text.secondary">暂无判定流水</Typography>;
  }
  return (
    <List dense disablePadding>
      {rows.map((e, i) => (
        <ListItem key={`${e.serial}-${e.frame_id}-${e.ts}-${i}`} disableGutters sx={{ py: 0.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', width: '100%' }}>
            <Typography variant="caption" sx={{ fontFamily: 'monospace', width: 150, flexShrink: 0 }}>
              {new Date(e.ts).toLocaleTimeString()}
            </Typography>
            <StateBadge result={e.result} compact />
            <Typography variant="caption">
              cov {(e.coverage * 100).toFixed(1)}% · {e.elapsed_ms}ms · f{e.frame_id}
            </Typography>
            {e.warnings.length > 0 && (
              <Typography variant="caption" color="warning.main">
                ⚠ {e.warnings.join(',')}
              </Typography>
            )}
          </Box>
        </ListItem>
      ))}
    </List>
  );
}
