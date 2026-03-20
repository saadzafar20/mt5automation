/* pywebview JS bridge wrapper */

interface JsBridgeApi {
  get_keyring_password(service: string, userId: string): Promise<string>;
  set_keyring_password(service: string, userId: string, pw: string): Promise<void>;
  browse_file(title: string, startDir: string, filter: string): Promise<string>;
  detect_mt5_path(): Promise<string>;
  is_startup_enabled(): Promise<boolean>;
  enable_startup(): Promise<void>;
  disable_startup(): Promise<void>;
  set_clipboard(text: string): Promise<void>;
  get_last_user(): Promise<string>;
  save_last_user(dataJson: string): Promise<void>;
  open_external(url: string): Promise<void>;
}

declare global {
  interface Window {
    pywebview?: { api: JsBridgeApi };
  }
}

let ready = false;

function waitForBridge(): Promise<JsBridgeApi> {
  if (ready && window.pywebview) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    if (window.pywebview) {
      ready = true;
      resolve(window.pywebview.api);
    } else {
      window.addEventListener('pywebviewready', () => {
        ready = true;
        resolve(window.pywebview!.api);
      }, { once: true });
    }
  });
}

async function call<T>(method: keyof JsBridgeApi, ...args: unknown[]): Promise<T> {
  try {
    const api = await waitForBridge();
    return (api[method] as (...a: unknown[]) => Promise<T>)(...args);
  } catch {
    console.warn(`Bridge call ${method} failed — running outside pywebview?`);
    return undefined as T;
  }
}

export const bridge = {
  getKeyringPassword: (svc: string, uid: string) => call<string>('get_keyring_password', svc, uid),
  setKeyringPassword: (svc: string, uid: string, pw: string) => call<void>('set_keyring_password', svc, uid, pw),
  browseFile: (title: string, startDir: string, filter: string) => call<string>('browse_file', title, startDir, filter),
  detectMt5Path: () => call<string>('detect_mt5_path'),
  isStartupEnabled: () => call<boolean>('is_startup_enabled'),
  enableStartup: () => call<void>('enable_startup'),
  disableStartup: () => call<void>('disable_startup'),
  setClipboard: (text: string) => call<void>('set_clipboard', text),
  getLastUser: () => call<string>('get_last_user'),
  saveLastUser: (data: string) => call<void>('save_last_user', data),
  openExternal: (url: string) => call<void>('open_external', url),
  isAvailable: () => !!window.pywebview,
};
