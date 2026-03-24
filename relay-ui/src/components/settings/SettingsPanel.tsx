import { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Power, Bell, Volume2, Info, Trash2 } from 'lucide-react';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { ScrollReveal } from '../ui/ScrollReveal';
import { useAppStore } from '../../store/appStore';
import { bridge } from '../../lib/bridge';
import { APP_VERSION, BRIDGE_URL } from '../../lib/constants';

function usePersistentToggle(key: string, defaultValue = true) {
  const [value, setValue] = useState(() => {
    const stored = localStorage.getItem(key);
    return stored !== null ? stored === 'true' : defaultValue;
  });
  const toggle = () => {
    setValue((prev) => {
      const next = !prev;
      localStorage.setItem(key, String(next));
      return next;
    });
  };
  return [value, toggle] as const;
}

function Toggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <motion.button
      className={`w-10 h-5 rounded-full relative cursor-pointer transition-colors duration-200 border-none shrink-0 ${enabled ? 'bg-success' : 'bg-bg-input border border-border'}`}
      onClick={onToggle}
      whileTap={{ scale: 0.95 }}
    >
      <motion.div
        className="w-4 h-4 rounded-full bg-white absolute top-0.5"
        animate={{ left: enabled ? 22 : 2 }}
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
      />
    </motion.button>
  );
}

export function SettingsPanel() {
  const [startup, setStartup] = useState(false);
  const [sound, toggleSound] = usePersistentToggle('setting-sound', true);
  const [desktop, toggleDesktop] = usePersistentToggle('setting-desktop', true);
  const [tray, toggleTray] = usePersistentToggle('setting-tray', true);
  const logs = useAppStore((s) => s.logs);
  const storeClearLogs = useAppStore((s) => s.clearLogs);
  const logRef = useRef<HTMLDivElement>(null);
  const [appVersion, setAppVersion] = useState(APP_VERSION);

  useEffect(() => {
    bridge.isStartupEnabled().then((v) => v !== undefined && setStartup(v));
    window.electronBridge?.getVersion().then((v) => { if (v) setAppVersion(v); });
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const toggleStartup = async () => {
    try {
      if (startup) await bridge.disableStartup();
      else await bridge.enableStartup();
      setStartup(!startup);
    } catch {
      // Bridge not available (web mode) — ignore
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <ScrollReveal variant="fade-up">
        <h1 className="text-2xl font-bold text-fg mb-2">Settings</h1>
        <p className="text-sm text-fg-muted">Configure your relay preferences</p>
      </ScrollReveal>

      {/* General */}
      <ScrollReveal variant="fade-up" delay={0.05}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-5 flex items-center gap-2">
            <Power size={16} className="text-accent" />
            General
          </h3>
          <div className="space-y-4">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-fg-muted">Launch on startup</span>
              <Toggle enabled={startup} onToggle={toggleStartup} />
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-fg-muted">Minimize to system tray on close</span>
              <Toggle enabled={tray} onToggle={toggleTray} />
            </label>
          </div>
        </Card>
      </ScrollReveal>

      {/* Notifications */}
      <ScrollReveal variant="fade-up" delay={0.1}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-5 flex items-center gap-2">
            <Bell size={16} className="text-accent" />
            Notifications
          </h3>
          <div className="space-y-4">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-fg-muted flex items-center gap-2">
                <Volume2 size={14} />
                Play sound on trade execution
              </span>
              <Toggle enabled={sound} onToggle={toggleSound} />
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-fg-muted flex items-center gap-2">
                <Bell size={14} />
                Show desktop notifications
              </span>
              <Toggle enabled={desktop} onToggle={toggleDesktop} />
            </label>
          </div>
        </Card>
      </ScrollReveal>

      {/* Relay Log */}
      <ScrollReveal variant="fade-up" delay={0.15}>
        <Card>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-fg">Relay Log</h3>
            <OutlineButton size="sm" onClick={storeClearLogs}>
              <Trash2 size={12} className="mr-1" />
              Clear
            </OutlineButton>
          </div>
          <div
            ref={logRef}
            className="bg-bg-input border border-border rounded-[var(--radius)] p-3 h-[200px] overflow-y-auto text-[0.675rem] text-fg-muted leading-relaxed"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {logs.length === 0 ? (
              <span className="text-fg-faint">No log entries yet</span>
            ) : (
              logs.map((line, i) => <div key={i}>{line}</div>)
            )}
          </div>
        </Card>
      </ScrollReveal>

      {/* About */}
      <ScrollReveal variant="fade-up" delay={0.2}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <Info size={16} className="text-accent" />
            About
          </h3>
          <div className="text-sm text-fg-muted space-y-2">
            <div>Version: <span className="text-fg font-mono">{appVersion}</span></div>
            <div>Bridge: <span className="text-fg font-mono">{BRIDGE_URL}</span></div>
            <div className="pt-2 text-fg-faint">
              <span className="text-accent font-semibold">Plat</span>
              <span className="text-fg font-semibold">Algo</span>
              <span className="ml-1">— Automated Trading Platform</span>
            </div>
          </div>
        </Card>
      </ScrollReveal>
    </div>
  );
}
