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
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
          <Cloud size={16} className="text-accent" />
          Cloud Execution
        </h2>
        <span className="text-[0.5rem] font-bold text-[hsl(155,40%,10%)] bg-accent px-2 py-0.5 rounded-full uppercase tracking-wider whitespace-nowrap">
          Recommended
        </span>
      </div>

      {vpsActive && (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-success-bg border border-success/20 text-xs text-success font-medium mb-4">
          <Check size={14} />
          Cloud Active — 24/7 Execution
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {features.map(({ icon: Icon, text }, i) => (
          <motion.div
            key={i}
            className="flex items-center gap-2.5 text-xs text-fg-muted py-1"
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
          >
            <Icon size={14} className="text-success shrink-0 transition-all duration-300 hover:scale-125 hover:text-accent hover:drop-shadow-[0_0_6px_var(--color-accent-glow)]" />
            {text}
          </motion.div>
        ))}
      </div>

      <p className="text-xs text-fg-muted mt-5 leading-relaxed">
        Enter your MT5 credentials on the left and click <span className="text-accent font-medium">"Login to MT5 on Cloud"</span> to enable 24/7 automated execution without keeping your PC on.
      </p>
    </Card>
  );
}
