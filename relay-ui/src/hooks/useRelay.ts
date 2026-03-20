import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { relayState } from '../lib/api';

export function useRelayPolling(intervalMs = 2000) {
  const setRelayStatus = useAppStore((s) => s.setRelayStatus);
  const setRelayDots = useAppStore((s) => s.setRelayDots);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const addLog = useAppStore((s) => s.addLog);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    async function poll() {
      try {
        const data = await relayState();
        setRelayStatus(data.status || 'Idle');
        setRelayDots({
          bridge: data.bridge_online ? 'online' : 'offline',
          mt5: data.mt5_online ? 'online' : 'offline',
          broker: data.broker_online ? 'online' : 'offline',
        });
        setVpsActive(data.vps_active || false);
        if (data.logs) {
          for (const line of data.logs) addLog(line);
        }
      } catch (_e) {
        // local API not running
      }
    }

    poll();
    intervalRef.current = setInterval(poll, intervalMs);
    return () => clearInterval(intervalRef.current);
  }, [intervalMs, setRelayStatus, setRelayDots, setVpsActive, addLog]);
}
