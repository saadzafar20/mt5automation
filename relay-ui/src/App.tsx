import { useThemeInit } from './hooks/useTheme';
import { useRelayPolling } from './hooks/useRelay';
import { AppShell } from './components/layout/AppShell';

export default function App() {
  useThemeInit();
  useRelayPolling();
  return <AppShell />;
}
