/**
 * 数据桥：按设置连接聚合 API（设备清单 + SSE + 状态轮询），
 * 任何失败统一降级到 Demo 模拟数据源（内置设备 + 模拟事件流）。
 */
import { useEffect, useRef } from 'react';
import {
  fetchDevices,
  fetchDeviceStatus,
  openEventStream,
} from '@/api/client';
import { MOCK_DEVICES, mockDeviceStatus, startMockFeed } from '@/api/mock';
import { useConsoleStore } from './store';

export function useBridge(): void {
  const settings = useConsoleStore((s) => s.settings);
  const forcedDemo = settings.demoMode;
  const baseUrl = settings.baseUrl;
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    const store = useConsoleStore.getState();
    const stops: Array<() => void> = [];
    store.reset();
    stopRef.current?.();
    stopRef.current = null;

    const startDemo = (): void => {
      if (cancelled) return;
      const s = useConsoleStore.getState();
      s.setDemoActive(true);
      s.ingestDevices(MOCK_DEVICES);
      for (const d of MOCK_DEVICES) {
        s.markConn(d.serial, 'online');
        s.ingestStatus(d.serial, mockDeviceStatus(d.serial));
      }
      const feed = startMockFeed((e) => useConsoleStore.getState().ingestEvent(e));
      stops.push(feed.close);
      stopRef.current = () => stops.forEach((fn) => fn());
    };

    const startLive = async (): Promise<void> => {
      // 1) 设备清单
      let devices;
      try {
        devices = await fetchDevices(baseUrl);
        if (!Array.isArray(devices) || devices.length === 0) {
          throw new Error('设备清单为空');
        }
      } catch (err) {
        console.warn('[bridge] 设备清单获取失败，降级 Demo:', (err as Error).message);
        startDemo();
        return;
      }
      if (cancelled) return;
      useConsoleStore.getState().ingestDevices(devices);

      // 2) SSE 事件流（EventSource 自带重连；onError 标记 offline）
      const handle = openEventStream(
        baseUrl,
        (e) => useConsoleStore.getState().ingestEvent(e),
        (err) => {
          console.warn('[bridge] SSE 错误:', err.message);
          const s = useConsoleStore.getState();
          for (const d of devices) s.markConn(d.serial, 'offline');
        },
      );
      stops.push(handle.close);

      // 3) 状态轮询（10s）：单台失败只标记该台离线，整体不降级
      const poll = async (): Promise<void> => {
        for (const d of devices) {
          try {
            const status = await fetchDeviceStatus(baseUrl, d.serial);
            useConsoleStore.getState().ingestStatus(d.serial, status);
            useConsoleStore.getState().markConn(d.serial, 'online');
          } catch {
            useConsoleStore.getState().markConn(d.serial, 'offline');
          }
        }
      };
      void poll();
      const timer = setInterval(() => void poll(), 10000);
      stops.push(() => clearInterval(timer));

      stopRef.current = () => stops.forEach((fn) => fn());
    };

    if (forcedDemo) {
      startDemo();
    } else {
      void startLive();
    }

    return () => {
      cancelled = true;
      stops.forEach((fn) => fn());
      stopRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forcedDemo, baseUrl]);
}
