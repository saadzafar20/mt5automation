import { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Power, FolderOpen, Bell, Volume2, Info, Trash2 } from 'lucide-react';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { ScrollReveal } from '../ui/ScrollReveal';
import { useAppStore } from '../../store/appStore';
import { bridge } from '../../lib/bridge';
import { clearLogs } from '../../lib/api';
import { APP_VERSION, BRIDGE_URL } from '../../lib/constants';

export function SettingsPanel() {
  const [startup, setStartup] = useState(false);
  const [tray, setTray] = useState(true);
  const [sound, setSound] = useState(true);
  const [desktop, setDesktop] = useState(true);
  const [mt5Path, setMt5Path] = useState('');
  const logs = useAppStore((s) => s.logs);
  const storeClearLogs = useAppStore((s) => s.clearLogs);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bridge.isStartupEnabled().then((v) => v !== undefined && setStartup(v));
    bridge.detectMt5Path().then((v) => v && setMt5Path(v));
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const toggleStartup = async () => {
    if (startup) {
      await bridge.disableStartup();
    } else {
      await bridge.enableStartup();
    }
    setStartup(!startup);
  };

  const browseMt5 = async () => {
    const path = await bridge.browseFile('Select MT5 Terminal', 'C:\\Program Files', '*.exe');
    if (path) setMt5Path(path);
  };

  const handleClearLogs = async () => {
    storeClearLogs();
    await clearLogs();
  };

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <ScrollReveal variant="fade-up">
        <h1 className="text-xl font-bold text-fg mb-1">Settings</h1>
        <p className="text-sm text-fg-muted">Configure your relay preferences</p>
      </ScrollReveal>

      {/* General */}
      <ScrollReveal variant="fade-up" delay={0.05}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <Power size={16} className="text-accent transition-all duration-300 hover:scale-125 hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            General
          </h3>
          <div className="space-y-3">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-xs text-fg-muted">Launch on startup</span>
              <motion.button
                className={`w-10 h-5 rounded-full relative cursor-pointer transition-colors duration-200 border-none ${startup ? 'bg-success' : 'bg-bg-input border border-border'}`}
                onClick={toggleStartup}
                whileTap={{ scale: 0.95 }}
              >
                <motion.div
                  className="w-4 h-4 rounded-full bg-white absolute top-0.5"
                  animate={{ left: startup ? 22 : 2 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                />
              </motion.button>
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-xs text-fg-muted">Minimize to system tray on close</span>
              <motion.button
                className={`w-10 h-5 rounded-full relative cursor-pointer transition-colors duration-200 border-none ${tray ? 'bg-success' : 'bg-bg-input border border-border'}`}
                onClick={() => setTray(!tray)}
                whileTap={{ scale: 0.95 }}
              >
                <motion.div
                  className="w-4 h-4 rounded-full bg-white absolute top-0.5"
                  animate={{ left: tray ? 22 : 2 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                />
              </motion.button>
            </label>
          </div>
        </Card>
      </ScrollReveal>

      {/* MT5 Path */}
      <ScrollReveal variant="fade-up" delay={0.1}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <FolderOpen size={16} className="text-accent transition-all duration-300 hover:scale-125 hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            MT5 Terminal
            <span className="text-[0.5rem] font-bold text-fg-muted bg-bg-hover px-1.5 py-0.5 rounded">WINDOWS ONLY</span>
          </h3>
          <div className="flex gap-2">
            <input
              readOnly
              value={mt5Path || 'Auto-detected or browse...'}
              className="flex-1 bg-bg-input border border-border text-fg text-xs px-3 py-2 rounded-[var(--radius)] outline-none font-mono"
            />
            <OutlineButton size="sm" onClick={browseMt5}>Browse</OutlineButton>
          </div>
        </Card>
      </ScrollReveal>

      {/* Notifications */}
      <ScrollReveal variant="fade-up" delay={0.15}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <Bell size={16} className="text-accent transition-all duration-300 hover:scale-125 hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            Notifications
          </h3>
          <div className="space-y-3">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-xs text-fg-muted flex items-center gap-2">
                <Volume2 size={14} />
                Play sound on trade execution
              </span>
              <motion.button
                className={`w-10 h-5 rounded-full relative cursor-pointer transition-colors duration-200 border-none ${sound ? 'bg-success' : 'bg-bg-input border border-border'}`}
                onClick={() => setSound(!sound)}
                whileTap={{ scale: 0.95 }}
              >
                <motion.div
                  className="w-4 h-4 rounded-full bg-white absolute top-0.5"
                  animate={{ left: sound ? 22 : 2 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                />
              </motion.button>
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-xs text-fg-muted flex items-center gap-2">
                <Bell size={14} />
                Show desktop notifications
              </span>
              <motion.button
                className={`w-10 h-5 rounded-full relative cursor-pointer transition-colors duration-200 border-none ${desktop ? 'bg-success' : 'bg-bg-input border border-border'}`}
                onClick={() => setDesktop(!desktop)}
                whileTap={{ scale: 0.95 }}
              >
                <motion.div
                  className="w-4 h-4 rounded-full bg-white absolute top-0.5"
                  animate={{ left: desktop ? 22 : 2 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                />
              </motion.button>
            </label>
          </div>
        </Card>
      </ScrollReveal>

      {/* Relay Log */}
      <ScrollReveal variant="fade-up" delay={0.2}>
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-fg">Relay Log</h3>
            <OutlineButton size="sm" onClick={handleClearLogs}>
              <Trash2 size={12} className="mr-1" />
              Clear
            </OutlineButton>
          </div>
          <div
            ref={logRef}
            className="bg-bg-input border border-border rounded-[var(--radius)] p-3 h-[200px] overflow-y-auto font-mono text-[0.6875rem] text-fg-muted leading-relaxed"
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
      <ScrollReveal variant="fade-up" delay={0.25}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-3 flex items-center gap-2">
            <Info size={16} className="text-accent transition-all duration-300 hover:scale-125 hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            About
          </h3>
          <div className="text-xs text-fg-muted space-y-1.5">
            <div>Version: <span className="text-fg font-mono">{APP_VERSION}</span></div>
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
