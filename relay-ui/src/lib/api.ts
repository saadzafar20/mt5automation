import { BRIDGE_URL } from './constants';

/* ── Auth helpers ── */

function authHeaders(userId: string, apiKey: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-User-ID': userId,
    'X-API-Key': apiKey,
  };
}

/* ── Cloud Bridge API ── */

export async function startOAuth(provider: 'google' | 'facebook', inviteCode?: string) {
  const res = await fetch(`${BRIDGE_URL}/auth/desktop/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, invite_code: (inviteCode || '').trim() }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{ auth_url: string; state: string }>;
}

export async function consumeOAuth(state: string) {
  const res = await fetch(`${BRIDGE_URL}/auth/desktop/consume/${state}`);
  if (res.status === 202) return null; // still waiting
  if (!res.ok) return null;
  return res.json() as Promise<{ user_id: string; api_key: string }>;
}

export async function getDashboardSummary(
  userId: string,
  apiKey: string,
  relayToken?: string,
  relayId?: string,
) {
  // C2: Support relay_token auth as fallback when api_key is not yet available
  const body: Record<string, string> = { user_id: userId };
  if (apiKey) {
    body.api_key = apiKey;
  } else if (relayToken && relayId) {
    body.relay_token = relayToken;
    body.relay_id = relayId;
  }
  const res = await fetch(`${BRIDGE_URL}/dashboard/summary/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ── Managed/Cloud MT5 API ── */

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
    body.user_id = params.user_id;
    body.password = params.password;
    url = `${BRIDGE_URL}/managed/setup/login`;
  } else {
    headers['X-User-ID'] = params.user_id;
    if (params.api_key) headers['X-API-Key'] = params.api_key;
    url = `${BRIDGE_URL}/managed/setup`;
  }

  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  if (!res.ok) {
    let err = `HTTP ${res.status}`;
    try { const j = await res.json(); err = j.error || err; } catch { /* non-JSON error */ }
    return { error: err };
  }
  return res.json();
}

export async function managedDisable(userId: string, apiKey?: string) {
  const headers: Record<string, string> = { 'X-User-ID': userId };
  if (apiKey) headers['X-API-Key'] = apiKey;
  const res = await fetch(`${BRIDGE_URL}/managed/disable`, { method: 'POST', headers });
  if (!res.ok) {
    let err = `HTTP ${res.status}`;
    try { const j = await res.json(); err = j.error || err; } catch { /* non-JSON */ }
    return { error: err };
  }
  return res.json();
}

export async function managedStatus(userId: string, apiKey?: string) {
  const headers: Record<string, string> = { 'X-User-ID': userId };
  if (apiKey) headers['X-API-Key'] = apiKey;
  const res = await fetch(`${BRIDGE_URL}/managed/status`, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{
    configured: boolean;
    connected: boolean;
    managed_execution: boolean;
  }>;
}

/* ── Telegram API (authenticated with API key headers) ── */

export async function getTelegramChannels(userId: string, apiKey: string) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/channels`, {
    headers: authHeaders(userId, apiKey),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{
    channels: Array<{
      channel_id: string; chat_id: string; chat_title: string;
      enabled: number; risk_pct: number; max_trades_per_day: number;
      allowed_symbols: string | null;
    }>;
    bot_running: boolean;
    bot_username: string;
  }>;
}

export async function addTelegramChannel(userId: string, apiKey: string, data: {
  chat_id: string; risk_pct: number; max_trades_per_day: number; allowed_symbols: string | null;
}) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/channels`, {
    method: 'POST',
    headers: authHeaders(userId, apiKey),
    body: JSON.stringify({ ...data, script_name: 'Telegram' }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function toggleTelegramChannel(userId: string, apiKey: string, channelId: string) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/channels/${channelId}/toggle`, {
    method: 'POST',
    headers: authHeaders(userId, apiKey),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteTelegramChannel(userId: string, apiKey: string, channelId: string) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/channels/${channelId}`, {
    method: 'DELETE',
    headers: authHeaders(userId, apiKey),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getTelegramSignals(userId: string, apiKey: string, limit = 30) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/signals?limit=${limit}`, {
    headers: authHeaders(userId, apiKey),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{ signals: Array<{
    log_id: string; raw_text: string; parsed_action: string; parsed_symbol: string;
    parse_confidence: number; execution_status: string; created_at: number;
  }> }>;
}

export async function testTelegramParse(userId: string, apiKey: string, text: string, useLlm: boolean) {
  const res = await fetch(`${BRIDGE_URL}/api/telegram/test-parse`, {
    method: 'POST',
    headers: authHeaders(userId, apiKey),
    body: JSON.stringify({ text, use_llm: useLlm }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
