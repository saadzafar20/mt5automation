import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  Link2, LayoutDashboard, TrendingUp, BookOpen, Settings, Send,
} from 'lucide-react';
import { useAppStore, type Tab } from '../../store/appStore';
import { APP_VERSION } from '../../lib/constants';

const navItems: { tab: Tab; icon: typeof Link2; label: string }[] = [
  { tab: 'connect', icon: Link2, label: 'Connect' },
  { tab: 'dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { tab: 'tradingview', icon: TrendingUp, label: 'TradingView' },
  { tab: 'telegram', icon: Send, label: 'Telegram' },
  { tab: 'guide', icon: BookOpen, label: 'Guide' },
  { tab: 'settings', icon: Settings, label: 'Settings' },
];

export function Sidebar() {
  const activeTab = useAppStore((s) => s.activeTab);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const vpsActive = useAppStore((s) => s.vpsActive);
  const [appVersion, setAppVersion] = useState(APP_VERSION);

  useEffect(() => {
    window.electronBridge?.getVersion().then((v) => { if (v) setAppVersion(v); });
  }, []);

  return (
    <aside className="w-[220px] shrink-0 bg-bg-sidebar border-r border-border flex flex-col py-6 z-20">
      <nav className="flex flex-col gap-1 px-3 flex-1 justify-center">
        {navItems.map(({ tab, icon: Icon, label }) => {
          const isActive = activeTab === tab;
          return (
            <motion.button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`
                flex items-center gap-4 pl-4 pr-3 py-3.5 rounded-lg text-[0.95rem] font-medium
                cursor-pointer transition-all duration-200 w-full text-left relative
                ${isActive
                  ? 'text-fg border-l-2 border-l-accent'
                  : 'text-fg-muted hover:text-fg border-l-2 border-l-transparent'}
              `}
              style={isActive ? {
                background: 'linear-gradient(90deg, hsla(155,65%,36%,0.13) 0%, transparent 80%)',
              } : undefined}
              whileHover={!isActive ? { x: 3 } : {}}
              whileTap={{ scale: 0.97 }}
            >
              <Icon
                size={20}
                className={`shrink-0 transition-all duration-300 ${isActive
                  ? 'text-accent drop-shadow-[0_0_5px_var(--color-accent-glow)]'
                  : 'text-fg-muted'}`}
              />
              <span style={{ letterSpacing: '-0.005em' }}>{label}</span>
            </motion.button>
          );
        })}
      </nav>

      <div className="px-3 space-y-2.5">
        {vpsActive && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-success-bg border border-success/20 text-xs text-success font-medium">
            <div className="w-1.5 h-1.5 rounded-full bg-success animate-[pulse-dot_2s_ease-in-out_infinite] shrink-0" />
            VPS Active — 24/7
          </div>
        )}
        <div className="text-[0.625rem] text-fg-faint/60 px-3 font-medium" style={{ letterSpacing: '0.06em' }}>
          v{appVersion}
        </div>
      </div>
    </aside>
  );
}
