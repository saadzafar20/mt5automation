import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { BRIDGE_URL } from '../lib/constants';

export function useRelayPolling(intervalMs = 5000) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    async function poll() {
      const { auth, setRelayDots, setRelayStatus, setVpsActive } = useAppStore.getState();

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

        // Bridge is online if we reached the cloud
        setRelayDots({ bridge: 'online' });

        if (relayIds.length > 0) {
          // Check any relay — state is "active" when online
          const anyOnline = relayIds.some((id) => {
            const r = relays[id];
            return r?.state === 'active' || r?.state === 'online';
          });
          setRelayDots({
            bridge: 'online',
            mt5: anyOnline ? 'online' : 'offline',
            broker: anyOnline ? 'online' : 'offline',
          });
          setRelayStatus(anyOnline ? 'Connected' : 'Idle');
        } else {
          setRelayDots({ bridge: 'online', mt5: 'offline', broker: 'offline' });
          setRelayStatus('Idle');
        }

        // Check if any managed relay exists (prefix "managed-")
        const hasManagedRelay = relayIds.some((id) => id.startsWith('managed-'));
        setVpsActive(hasManagedRelay);
      } catch {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Offline');
      }
    }

    poll();
    intervalRef.current = setInterval(poll, intervalMs);
    return () => clearInterval(intervalRef.current);
  }, [intervalMs]);
}
