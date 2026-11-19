import { HashRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import { AlertBanner } from '@/features/alerts/AlertBanner';
import { DeviceWall } from '@/features/device-wall/DeviceWall';
import { LiveView } from '@/features/live-view/LiveView';
import { HistoryPage } from '@/features/history/HistoryPage';
import { AlertsPage } from '@/features/alerts/AlertsPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { useBridge } from '@/store/useBridge';
import { useConsoleStore } from '@/store/store';

const NAV_ITEMS = [
  { to: '/', label: '设备墙' },
  { to: '/history', label: '历史' },
  { to: '/alerts', label: '告警' },
  { to: '/settings', label: '设置' },
];

function Shell() {
  useBridge(); // 全局数据源：聚合 API 或 Demo 降级
  const demoActive = useConsoleStore((s) => s.demoActive);

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-6">
          <span className="font-extrabold text-lg tracking-wide text-slate-800">
            MoiréGuard Console
          </span>
          <nav className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="px-3 py-1.5 rounded text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <span className="ml-auto text-xs text-slate-500">
            {demoActive ? 'DEMO 模拟模式' : 'LIVE 聚合模式'}
          </span>
        </div>
      </header>
      <AlertBanner />
      <main>
        <Routes>
          <Route path="/" element={<DeviceWall />} />
          <Route path="/live/:serial" element={<LiveView />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <Shell />
    </HashRouter>
  );
}
