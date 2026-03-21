import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { BRIDGE_URL } from '../lib/constants';

export function useRelayPolling(intervalMs = 5000) {
  const auth = useAppStore((s) => s.auth);
  const setRelayStatus = useAppStore((s) => s.setRelayStatus);
  const setRelayDots = useAppStore((s) => s.setRelayDots);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    async function poll() {
      if (!auth.userId || !auth.apiKey) {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Idle');
        return;
      }

      try {
        const res = await fetch(`${BRIDGE_URL}/dashboard/summary/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: auth.userId, api_key: auth.apiKey }),
        });

        if (!res.ok) {
          setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
          setRelayStatus('Idle');
          return;
        }

        const data = await res.json();
        const dashboard = data.dashboard || {};
        const relays = dashboard.relays || {};
        const relayIds = Object.keys(relays);

        // Bridge is online if we can reach the cloud
        setRelayDots({ bridge: 'online' });

        if (relayIds.length > 0) {
          const relay = relays[relayIds[0]];
          const isOnline = relay?.status === 'online';
          setRelayDots({
            bridge: 'online',
            mt5: isOnline ? 'online' : 'offline',
            broker: isOnline ? 'online' : 'offline',
          });
          setRelayStatus(isOnline ? 'Connected' : 'Idle');
        } else {
          setRelayDots({ bridge: 'online', mt5: 'offline', broker: 'offline' });
          setRelayStatus('Idle');
        }

        // Check VPS/managed mode
        const managed = data.settings?.managed_enabled;
        setVpsActive(!!managed);
      } catch {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Offline');
      }
    }

    poll();
    intervalRef.current = setInterval(poll, intervalMs);
    return () => clearInterval(intervalRef.current);
  }, [intervalMs, auth.userId, auth.apiKey, setRelayStatus, setRelayDots, setVpsActive]);
}
