import { BRIDGE_URL } from './constants';

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

/* ── Managed/Cloud MT5 API (calls cloud bridge directly) ── */

export async function managedEnable(params: {
  user_id: string;
  api_key?: string;
  password?: string;
  mt5_login: string;
  mt5_password: string;
  mt5_server: string;
}) {
  const body: Record<string, string> = {
    mt5_login: params.mt5_login,
    mt5_password: params.mt5_password,
    mt5_server: params.mt5_server,
  };

  let url: string;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };

  if (params.password) {
    // Login-based auth
    body.user_id = params.user_id;
    body.password = params.password;
    url = `${BRIDGE_URL}/managed/setup/login`;
  } else {
    // API key auth
    headers['X-User-ID'] = params.user_id;
    if (params.api_key) headers['X-API-Key'] = params.api_key;
    url = `${BRIDGE_URL}/managed/setup`;
  }

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function managedDisable(userId: string, apiKey?: string) {
  const res = await fetch(`${BRIDGE_URL}/relay/managed/disable`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': userId,
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    },
    body: JSON.stringify({ user_id: userId }),
  });
  return res.json();
}

export async function clearLogs() {
  // No-op in Electron mode (no local Flask)
  return { status: 'cleared' };
}
