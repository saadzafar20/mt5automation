/* Native bridge — uses Electron IPC when available, falls back to REST */

interface ElectronBridge {
  openExternal: (url: string) => Promise<boolean>;
  getPlatform: () => Promise<string>;
  getVersion: () => Promise<string>;
  keyringGet: (service: string, userId: string) => Promise<string>;
  keyringSet: (service: string, userId: string, password: string) => Promise<boolean>;
  clipboardWrite: (text: string) => Promise<boolean>;
  isStartupEnabled: () => Promise<boolean>;
  setStartup: (enabled: boolean) => Promise<boolean>;
  lastUserGet: () => Promise<Record<string, unknown>>;
  lastUserSet: (data: Record<string, unknown>) => Promise<boolean>;
}

declare global {
  interface Window {
    electronBridge?: ElectronBridge;
  }
}

const eb = () => window.electronBridge;

export const bridge = {
  getKeyringPassword: async (service: string, userId: string): Promise<string> => {
    if (eb()) return (await eb()!.keyringGet(service, userId)) || '';
    return '';
  },

  setKeyringPassword: async (service: string, userId: string, password: string): Promise<void> => {
    if (eb()) await eb()!.keyringSet(service, userId, password);
  },

  detectMt5Path: async (): Promise<string> => {
    // MT5 detection only relevant on Windows VPS, not in desktop app
    return '';
  },

  isStartupEnabled: async (): Promise<boolean> => {
    if (eb()) return await eb()!.isStartupEnabled();
    return false;
  },

  enableStartup: async (): Promise<void> => {
    if (eb()) await eb()!.setStartup(true);
  },

  disableStartup: async (): Promise<void> => {
    if (eb()) await eb()!.setStartup(false);
  },

  setClipboard: async (text: string): Promise<void> => {
    if (eb()) {
      await eb()!.clipboardWrite(text);
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch {
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
    if (eb()) {
      const data = await eb()!.lastUserGet();
      return data && Object.keys(data).length ? JSON.stringify(data) : '';
    }
    return '';
  },

  saveLastUser: async (dataJson: string): Promise<void> => {
    if (eb()) {
      await eb()!.lastUserSet(JSON.parse(dataJson));
    }
  },

  openExternal: async (url: string): Promise<void> => {
    if (eb()) {
      await eb()!.openExternal(url);
      return;
    }
    window.open(url, '_blank');
  },

  browseFile: async (_title: string, _startDir: string, _filter: string): Promise<string> => {
    return '';
  },

  isAvailable: () => !!eb(),
};
