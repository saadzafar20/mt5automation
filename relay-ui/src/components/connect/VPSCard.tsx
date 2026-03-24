import { motion } from 'framer-motion';
import { Cloud, Clock, Shield, Zap, Monitor, Server, Check } from 'lucide-react';
import { Card } from '../ui/Card';
import { useAppStore } from '../../store/appStore';

const features = [
  { icon: Clock, text: '24/7 trading, always on' },
  { icon: Cloud, text: 'No MT5 required on your PC' },
  { icon: Monitor, text: 'Works from any device' },
  { icon: Shield, text: 'DDoS-protected infrastructure' },
  { icon: Zap, text: 'Sub-millisecond execution' },
  { icon: Server, text: 'Dedicated resources' },
];

export function VPSCard() {
  const vpsActive = useAppStore((s) => s.vpsActive);

  return (
    <Card gold>
      <div className="flex items-center gap-3 mb-6">
        <Cloud size={18} className="text-accent shrink-0" />
        <h2 className="text-base font-semibold text-fg flex-1">Cloud Execution</h2>
        <span className="text-[0.6rem] font-bold text-bg bg-accent px-2.5 py-1 rounded-full uppercase tracking-wider shrink-0">
          Recommended
        </span>
      </div>

      {vpsActive && (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-success-bg border border-success/20 text-xs text-success font-medium mb-4">
          <Check size={14} />
          Cloud Active — 24/7 Execution
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-4 flex-1">
        {features.map(({ icon: Icon, text }, i) => (
          <motion.div
            key={i}
            className="flex items-center gap-2.5 text-[0.8rem] text-fg-muted"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
          >
            <Icon size={13} className="text-success shrink-0 transition-all duration-300 hover:scale-125 hover:text-accent hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            {text}
          </motion.div>
        ))}
      </div>

      {!vpsActive && (
        <p className="text-sm text-fg-muted mt-auto pt-6 leading-relaxed">
          Enter your MT5 credentials on the left and click <span className="text-accent font-medium">"Login to MT5 for 24/7 VPS Mode"</span> to enable 24/7 automated execution without keeping your PC on.
        </p>
      )}
    </Card>
  );
}
