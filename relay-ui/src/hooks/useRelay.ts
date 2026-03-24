import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { BRIDGE_URL } from '../lib/constants';

const POLL_FAST_MS = 3000;   // while VPS active but MT5 not yet online
const POLL_SLOW_MS = 10000;  // normal cadence

export function useRelayPolling() {
  const isPolling = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;

    async function poll() {
      if (isPolling.current) return;
      isPolling.current = true;

      const { auth, vpsActive, setRelayDots, setRelayStatus, setVpsActive, setDashboardData } =
        useAppStore.getState();

      if (!auth.userId || !auth.apiKey) {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Idle');
        isPolling.current = false;
        scheduleNext(POLL_SLOW_MS);
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
          scheduleNext(POLL_SLOW_MS);
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

        let mt5Connected = false;
        let brokerConnected = false;
        let anyRelayOnline = false;

        if (relayIds.length > 0) {
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

        // Adaptive interval: poll fast when VPS is active but MT5 hasn't connected yet
        const nextInterval =
          (hasManagedRelay || vpsActive) && !mt5Connected ? POLL_FAST_MS : POLL_SLOW_MS;
        isPolling.current = false;
        scheduleNext(nextInterval);
      } catch {
        setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
        setRelayStatus('Offline');
        isPolling.current = false;
        scheduleNext(POLL_SLOW_MS);
      }
    }

    function scheduleNext(ms: number) {
      if (cancelledRef.current) return;
      timerRef.current = setTimeout(() => {
        if (!cancelledRef.current) poll();
      }, ms);
    }

    // Short initial delay to let auth restoration from bridge.getLastUser() complete
    timerRef.current = setTimeout(() => {
      if (!cancelledRef.current) poll();
    }, 400);

    return () => {
      cancelledRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);
}
