import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { BRIDGE_URL } from '../lib/constants';

export function useRelayPolling(intervalMs = 10000) {
  const isPolling = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      // Prevent concurrent polls on slow connections
      if (isPolling.current) return;
      isPolling.current = true;

      const { auth, setRelayDots, setRelayStatus, setVpsActive, setDashboardData } =
        useAppStore.getState();

      if (!auth.userId || !auth.apiKey) {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Idle');
        isPolling.current = false;
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
          isPolling.current = false;
          return;
        }

        const data = await res.json();
        const dashboard = data.dashboard || {};
        const relays = dashboard.relays || {};
        const relayIds = Object.keys(relays);

        setDashboardData({
          webhookUrl: data.webhook_url || '',
          apiKey: data.api_key || auth.apiKey || '',
          relayOnline: dashboard.relay_online || 0,
          relayTotal: dashboard.relay_total || 0,
          scripts: dashboard.scripts || [],
        });

        setRelayDots({ bridge: 'online' });

        if (relayIds.length > 0) {
          let mt5Connected = false;
          let brokerConnected = false;
          let anyRelayOnline = false;

          for (const id of relayIds) {
            const r = relays[id];
            if (r?.state === 'online') {
              anyRelayOnline = true;
              const meta = r.metadata || {};
              if (meta.mt5_connected) mt5Connected = true;
              if (meta.broker_connected) brokerConnected = true;
            }
          }

          setRelayDots({
            bridge: 'online',
            mt5: mt5Connected ? 'online' : 'offline',
            broker: brokerConnected ? 'online' : 'offline',
          });
          setRelayStatus(anyRelayOnline ? 'Connected' : 'Idle');
        } else {
          setRelayDots({ bridge: 'online', mt5: 'offline', broker: 'offline' });
          setRelayStatus('Idle');
        }

        const hasManagedRelay = relayIds.some(
          (id) => id.startsWith('managed-') && relays[id]?.state === 'online'
        );
        setVpsActive(hasManagedRelay);
      } catch {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Offline');
      } finally {
        isPolling.current = false;
      }
    }

    // Short initial delay to let auth restoration from bridge.getLastUser() complete
    // before the first poll (avoids a brief "offline" flash on startup)
    const initialTimer = setTimeout(() => {
      if (!cancelled) poll();
    }, 400);

    const interval = setInterval(() => {
      if (!cancelled) poll();
    }, intervalMs);

    return () => {
      cancelled = true;
      clearTimeout(initialTimer);
      clearInterval(interval);
    };
  }, [intervalMs]);
}
