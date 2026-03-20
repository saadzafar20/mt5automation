import { BRIDGE_URL, LOCAL_API } from './constants';

/* ── Cloud Bridge API ── */

export async function startOAuth(provider: 'google' | 'facebook') {
  const res = await fetch(`${BRIDGE_URL}/auth/desktop/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider }),
  });
  return res.json() as Promise<{ auth_url: string; state: string }>;
}

export async function consumeOAuth(state: string) {
  const res = await fetch(`${BRIDGE_URL}/auth/desktop/consume/${state}`);
  if (res.status === 202) return null; // still waiting
  if (!res.ok) return null;
  return res.json() as Promise<{ user_id: string; api_key: string }>;
}

export async function getDashboardSummary(userId: string, apiKey: string) {
  const res = await fetch(`${BRIDGE_URL}/dashboard/summary/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, api_key: apiKey }),
  });
  return res.json();
}

export async function checkVersion() {
  const res = await fetch(`${BRIDGE_URL}/version`);
  return res.json();
}

/* ── Local Flask API ── */

export async function relayStart(params: {
  user_id: string;
  password?: string;
  api_key?: string;
  relay_type?: string;
  mt5_login?: string;
  mt5_password?: string;
  mt5_server?: string;
}) {
  const res = await fetch(`${LOCAL_API}/api/relay/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return res.json();
}

export async function relayStop() {
  const res = await fetch(`${LOCAL_API}/api/relay/stop`, { method: 'POST' });
  return res.json();
}

export async function relayState() {
  const res = await fetch(`${LOCAL_API}/api/relay/state`);
  return res.json();
}

export async function managedEnable(params: {
  user_id: string;
  api_key?: string;
  password?: string;
  mt5_login: string;
  mt5_password: string;
  mt5_server: string;
}) {
  const res = await fetch(`${LOCAL_API}/api/managed/enable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return res.json();
}

export async function managedDisable(userId: string) {
  const res = await fetch(`${LOCAL_API}/api/managed/disable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  });
  return res.json();
}

export async function clearLogs() {
  const res = await fetch(`${LOCAL_API}/api/relay/logs/clear`, { method: 'POST' });
  return res.json();
}
