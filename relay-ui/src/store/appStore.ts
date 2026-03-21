import { create } from 'zustand';

export type Tab = 'connect' | 'dashboard' | 'tradingview' | 'telegram' | 'guide' | 'settings';
export type ThemeMode = 'dark' | 'light';
export type DotStatus = 'online' | 'offline' | 'unknown';

interface AuthState {
  userId: string | null;
  apiKey: string | null;
  oauthProvider: string | null;
  avatar: string | null;
}

interface RelayDots {
  bridge: DotStatus;
  mt5: DotStatus;
  broker: DotStatus;
}

interface DashboardData {
  webhookUrl: string;
  apiKey: string;
  relayOnline: number;
  relayTotal: number;
  scripts: Array<{
    script_code: string;
    script_name: string;
    signals_count: number;
    executed_count: number;
  }>;
}

interface AppState {
  // Navigation
  activeTab: Tab;
  setActiveTab: (tab: Tab) => void;

  // Theme
  theme: ThemeMode;
  toggleTheme: () => void;
  setTheme: (t: ThemeMode) => void;

  // Auth
  auth: AuthState;
  setAuth: (auth: Partial<AuthState>) => void;
  clearAuth: () => void;

  // Relay
  relayStatus: string;
  relayDots: RelayDots;
  vpsActive: boolean;
  logs: string[];
  setRelayStatus: (s: string) => void;
  setRelayDots: (dots: Partial<RelayDots>) => void;
  setVpsActive: (v: boolean) => void;
  addLog: (line: string) => void;
  clearLogs: () => void;

  // Dashboard
  dashboardData: DashboardData | null;
  setDashboardData: (d: DashboardData) => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeTab: 'connect',
  setActiveTab: (tab) => set({ activeTab: tab }),

  theme: (localStorage.getItem('platalgo-theme') as ThemeMode) || 'dark',
  toggleTheme: () =>
    set((s) => {
      const next = s.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('platalgo-theme', next);
      document.documentElement.dataset.theme = next;
      return { theme: next };
    }),
  setTheme: (t) => {
    localStorage.setItem('platalgo-theme', t);
    document.documentElement.dataset.theme = t;
    set({ theme: t });
  },

  auth: { userId: null, apiKey: null, oauthProvider: null, avatar: null },
  setAuth: (auth) => set((s) => ({ auth: { ...s.auth, ...auth } })),
  clearAuth: () => set({ auth: { userId: null, apiKey: null, oauthProvider: null, avatar: null } }),

  relayStatus: 'Idle',
  relayDots: { bridge: 'offline', mt5: 'offline', broker: 'offline' },
  vpsActive: false,
  logs: [],
  setRelayStatus: (relayStatus) => set({ relayStatus }),
  setRelayDots: (dots) => set((s) => ({ relayDots: { ...s.relayDots, ...dots } })),
  setVpsActive: (vpsActive) => set({ vpsActive }),
  addLog: (line) => set((s) => ({ logs: [...s.logs.slice(-200), line] })),
  clearLogs: () => set({ logs: [] }),

  dashboardData: null,
  setDashboardData: (dashboardData) => set({ dashboardData }),
}));
