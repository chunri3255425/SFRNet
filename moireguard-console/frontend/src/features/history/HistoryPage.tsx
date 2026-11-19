/** 历史页：全量判定流水 + CSV 导出 + 清空 */
import { Box, Button, Paper, Typography } from '@mui/material';
import { useConsoleStore } from '@/store/store';
import { exportHistoryCsv } from './csv';
import { EventList } from './EventList';

export function HistoryPage() {
  const history = useConsoleStore((s) => s.history);
  const clearHistory = useConsoleStore((s) => s.clearHistory);

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h6">判定流水（{history.length} 条，内存 + localStorage 上限 2000）</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="contained"
            size="small"
            disabled={history.length === 0}
            onClick={() => exportHistoryCsv(history)}
          >
            导出 CSV
          </Button>
          <Button variant="outlined" size="small" color="error" onClick={clearHistory}>
            清空
          </Button>
        </Box>
      </Box>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <EventList maxRows={200} />
      </Paper>
    </Box>
  );
}
