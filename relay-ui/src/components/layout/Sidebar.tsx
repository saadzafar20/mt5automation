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

  return (
    <aside className="w-[240px] shrink-0 bg-bg-sidebar border-r border-border flex flex-col py-8 z-20">
      <nav className="flex flex-col gap-3 px-5 flex-1 justify-center">
        {navItems.map(({ tab, icon: Icon, label }) => {
          const isActive = activeTab === tab;
          return (
            <motion.button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`
                flex items-center gap-4 px-4 py-4 rounded-lg text-base font-medium
                cursor-pointer transition-all duration-200 border-none bg-transparent w-full text-left
                ${isActive
                  ? 'bg-primary/10 text-fg border-l-2 border-l-accent'
                  : 'text-fg-muted hover:text-fg hover:bg-bg-hover'}
              `}
              whileHover={{ x: 4 }}
              whileTap={{ scale: 0.97 }}
            >
              <Icon
                size={22}
                className={`transition-all duration-300 ${isActive
                  ? 'text-accent drop-shadow-[0_0_6px_var(--color-accent-glow)]'
                  : 'group-hover:text-accent'}`}
              />
              {label}
            </motion.button>
          );
        })}
      </nav>

      <div className="px-5 space-y-3">
        {vpsActive && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-success-bg border border-success/20 text-xs text-success font-medium">
            <div className="w-2 h-2 rounded-full bg-success animate-[pulse-dot_2s_ease-in-out_infinite]" />
            VPS Active — 24/7
          </div>
        )}
        <div className="text-[0.625rem] text-fg-faint px-3">v{APP_VERSION}</div>
      </div>
    </aside>
  );
}
