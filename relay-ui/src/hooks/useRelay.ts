import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { BRIDGE_URL } from '../lib/constants';

export function useRelayPolling(intervalMs = 5000) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    async function poll() {
      const { auth, setRelayDots, setRelayStatus, setVpsActive, setDashboardData } = useAppStore.getState();

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

        // Update dashboard data so DashboardPanel stays in sync
        setDashboardData({
          webhookUrl: data.webhook_url || '',
          apiKey: data.api_key || auth.apiKey || '',
          relayOnline: dashboard.relay_online || 0,
          relayTotal: dashboard.relay_total || 0,
          scripts: dashboard.scripts || [],
        });

        // Bridge is online if we reached the cloud
        setRelayDots({ bridge: 'online' });

        if (relayIds.length > 0) {
          // Check relay state AND metadata for MT5/broker connection
          let mt5Connected = false;
          let brokerConnected = false;
          let anyRelayOnline = false;

          for (const id of relayIds) {
            const r = relays[id];
            if (r?.state === 'online') {
              anyRelayOnline = true;
              // Relay metadata contains actual MT5/broker connection status
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
