/* Native bridge — REST calls to Flask backend on localhost:5199 */

const LOCAL_API = 'http://127.0.0.1:5199';

async function post<T>(path: string, body?: Record<string, unknown>): Promise<T> {
  try {
    const res = await fetch(`${LOCAL_API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    return await res.json();
  } catch {
    console.warn(`Bridge call ${path} failed`);
    return undefined as T;
  }
}

async function get<T>(path: string): Promise<T> {
  try {
    const res = await fetch(`${LOCAL_API}${path}`);
    return await res.json();
  } catch {
    console.warn(`Bridge call ${path} failed`);
    return undefined as T;
  }
}

export const bridge = {
  getKeyringPassword: async (service: string, userId: string): Promise<string> => {
    const data = await post<{ password: string }>('/api/bridge/keyring/get', { service, user_id: userId });
    return data?.password ?? '';
  },

  setKeyringPassword: async (service: string, userId: string, password: string): Promise<void> => {
    await post('/api/bridge/keyring/set', { service, user_id: userId, password });
  },

  detectMt5Path: async (): Promise<string> => {
    const data = await get<{ path: string }>('/api/bridge/detect-mt5');
    return data?.path ?? '';
  },

  isStartupEnabled: async (): Promise<boolean> => {
    const data = await get<{ enabled: boolean }>('/api/bridge/startup');
    return data?.enabled ?? false;
  },

  enableStartup: async (): Promise<void> => {
    await post('/api/bridge/startup');
  },

  disableStartup: async (): Promise<void> => {
    await fetch(`${LOCAL_API}/api/bridge/startup`, { method: 'DELETE' });
  },

  setClipboard: async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for non-secure contexts
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
  },

  getLastUser: async (): Promise<string> => {
    const data = await get<Record<string, unknown>>('/api/bridge/last-user');
    return data ? JSON.stringify(data) : '';
  },

  saveLastUser: async (dataJson: string): Promise<void> => {
    await fetch(`${LOCAL_API}/api/bridge/last-user`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: dataJson,
    });
  },

  openExternal: async (url: string): Promise<void> => {
    // Use Flask backend to open in system browser (window.open doesn't work in pywebview)
    await post('/api/bridge/open-external', { url });
  },

  browseFile: async (_title: string, _startDir: string, _filter: string): Promise<string> => {
    // File browsing not available in browser mode — user must type path manually
    return '';
  },

  isAvailable: () => true,
};
