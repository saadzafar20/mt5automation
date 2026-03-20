import { AnimatePresence, motion } from 'framer-motion';
import { useAppStore } from '../../store/appStore';
import { Header } from './Header';
import { Sidebar } from './Sidebar';
import { Particles } from './Particles';
import { ConnectPanel } from '../connect/ConnectPanel';
import { DashboardPanel } from '../dashboard/DashboardPanel';
import { TradingViewPanel } from '../tradingview/TradingViewPanel';
import { GuidePanel } from '../guide/GuidePanel';
import { SettingsPanel } from '../settings/SettingsPanel';

const panels = {
  connect: ConnectPanel,
  dashboard: DashboardPanel,
  tradingview: TradingViewPanel,
  guide: GuidePanel,
  settings: SettingsPanel,
};

export function AppShell() {
  const activeTab = useAppStore((s) => s.activeTab);
  const Panel = panels[activeTab];

  return (
    <div className="h-full flex flex-col relative">
      <Particles />
      <Header />
      <div className="flex flex-1 min-h-0 relative z-10">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-6 lg:p-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
            >
              <Panel />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
