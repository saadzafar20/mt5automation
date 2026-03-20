import { useState } from 'react';
import { motion } from 'framer-motion';
import { Cloud, Clock, Shield, Zap, Monitor, Server, Check } from 'lucide-react';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { OutlineButton } from '../ui/OutlineButton';
import { useAppStore } from '../../store/appStore';
import { managedEnable, managedDisable } from '../../lib/api';

const features = [
  { icon: Clock, text: '24/7 trading, always on' },
  { icon: Cloud, text: 'No MT5 required on your PC' },
  { icon: Monitor, text: 'Works from any device' },
  { icon: Shield, text: 'DDoS-protected infrastructure' },
  { icon: Zap, text: 'Sub-millisecond execution' },
  { icon: Server, text: 'Dedicated resources' },
];

export function VPSCard() {
  const auth = useAppStore((s) => s.auth);
  const vpsActive = useAppStore((s) => s.vpsActive);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const [loading, setLoading] = useState(false);

  const handleActivate = async () => {
    if (!auth.userId) return;
    setLoading(true);
    try {
      await managedEnable({
        user_id: auth.userId,
        api_key: auth.apiKey || undefined,
        mt5_login: '', mt5_password: '', mt5_server: '',
      });
      setVpsActive(true);
    } finally {
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    if (!auth.userId) return;
    setLoading(true);
    try {
      await managedDisable(auth.userId);
      setVpsActive(false);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card gold>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
          <Cloud size={16} className="text-accent" />
          VPS Execution
        </h2>
        <span className="text-[0.5rem] font-bold text-bg bg-accent px-2 py-0.5 rounded-full uppercase tracking-wider">
          Recommended
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-5">
        {features.map(({ icon: Icon, text }, i) => (
          <motion.div
            key={i}
            className="flex items-center gap-2 text-xs text-fg-muted"
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
          >
            <Icon size={14} className="text-success shrink-0 transition-all duration-300 hover:scale-125 hover:text-accent hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            {text}
          </motion.div>
        ))}
      </div>

      {vpsActive ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-success-bg border border-success/20 text-sm text-success font-medium">
            <Check size={16} />
            VPS Active — 24/7
          </div>
          <OutlineButton danger fullWidth onClick={handleDisconnect} disabled={loading}>
            Disconnect
          </OutlineButton>
        </div>
      ) : (
        <GoldButton fullWidth onClick={handleActivate} disabled={loading || !auth.userId}>
          {loading ? 'Activating...' : 'Login to MT5 on VPS →'}
        </GoldButton>
      )}
    </Card>
  );
}
