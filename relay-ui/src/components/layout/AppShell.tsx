import { AnimatePresence, motion } from 'framer-motion';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { useAppStore } from '../../store/appStore';
import { Header } from './Header';
import { Sidebar } from './Sidebar';
import { ConnectPanel } from '../connect/ConnectPanel';
import { DashboardPanel } from '../dashboard/DashboardPanel';
import { TradingViewPanel } from '../tradingview/TradingViewPanel';
import { TelegramPanel } from '../telegram/TelegramPanel';
import { GuidePanel } from '../guide/GuidePanel';
import { SettingsPanel } from '../settings/SettingsPanel';

const panels = {
  connect: ConnectPanel,
  dashboard: DashboardPanel,
  tradingview: TradingViewPanel,
  telegram: TelegramPanel,
  guide: GuidePanel,
  settings: SettingsPanel,
};

export function AppShell() {
  const activeTab = useAppStore((s) => s.activeTab);
  const Panel = panels[activeTab];

  return (
    <TooltipPrimitive.Provider delayDuration={400}>
      <div className="h-full flex flex-col relative">
        {/* Signature single-pixel gradient accent line at top — Linear/Raycast design pattern */}
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-primary/45 to-transparent z-50 pointer-events-none" />
        <Header />
        <div className="flex flex-1 min-h-0 relative z-10">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-6 lg:p-8 relative">
            {/* Depth shadow at scroll area top — separates content from header visually */}
            <div className="pointer-events-none absolute top-0 left-0 right-0 h-10 bg-gradient-to-b from-black/[0.07] to-transparent z-10" />
            <AnimatePresence mode="wait">
              <motion.div
                key={activeTab}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.22, ease: 'easeOut' }}
              >
                <Panel />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
    </TooltipPrimitive.Provider>
  );
}
