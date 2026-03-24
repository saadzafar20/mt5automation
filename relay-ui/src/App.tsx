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
  // F-01: user_id comes from last_user.json; sensitive tokens come from OS keychain.
  useEffect(() => {
    bridge.getLastUser().then(async (raw) => {
      if (!raw) return;
      try {
        const data = JSON.parse(raw) as Record<string, string>;
        if (data.user_id) {
          const userId = data.user_id;
          // Retrieve sensitive credentials from keychain (may be empty strings if not stored there)
          const [apiKey, relayToken, relayId] = await Promise.all([
            bridge.getKeyringPassword('platalgo-relay', userId + ':api_key'),
            bridge.getKeyringPassword('platalgo-relay', userId + ':relay_token'),
            bridge.getKeyringPassword('platalgo-relay', userId + ':relay_id'),
          ]);
          setAuth({
            userId,
            // Fall back to last_user.json values for users who signed in before this update
            apiKey: apiKey || data.api_key || null,
            relayToken: relayToken || data.relay_token || null,
            relayId: relayId || data.relay_id || null,
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
