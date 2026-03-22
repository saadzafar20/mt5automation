import { useEffect } from 'react';
import { useThemeInit } from './hooks/useTheme';
import { useRelayPolling } from './hooks/useRelay';
import { AppShell } from './components/layout/AppShell';
import { useAppStore } from './store/appStore';
import { bridge } from './lib/bridge';

export default function App() {
  useThemeInit();
  useRelayPolling();

  const setAuth = useAppStore((s) => s.setAuth);

  // Restore auth state persisted from previous session
  useEffect(() => {
    bridge.getLastUser().then((raw) => {
      if (!raw) return;
      try {
        const data = JSON.parse(raw) as Record<string, string>;
        if (data.user_id && data.api_key) {
          setAuth({
            userId: data.user_id,
            apiKey: data.api_key,
            oauthProvider: (data.oauth_provider as 'google' | 'facebook') || null,
          });
        }
      } catch {
        // ignore malformed persisted data
      }
    });
  }, [setAuth]);

  return <AppShell />;
}
