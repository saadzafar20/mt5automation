import { motion } from 'framer-motion';
import {
  LogIn, Server, KeyRound, Bell, SlidersHorizontal, BarChart3,
  ExternalLink, AlertTriangle,
} from 'lucide-react';
import { Card } from '../ui/Card';
import { ScrollReveal } from '../ui/ScrollReveal';
import { bridge } from '../../lib/bridge';

const steps = [
  {
    icon: LogIn,
    title: 'Sign In',
    items: [
      'Use Google or Facebook for quick access (recommended)',
      'Or enter your email and password',
      'Enable "Remember me" to auto-connect on startup',
    ],
  },
  {
    icon: Server,
    title: 'Choose Execution Mode',
    items: [
      'VPS Mode: 24/7 execution, no MT5 needed (recommended)',
      'Local Mode: Uses your own machine (requires Windows + MT5)',
    ],
  },
  {
    icon: KeyRound,
    title: 'Enter MT5 Credentials (VPS Mode)',
    items: [
      'Enter your MT5 account number, password, and broker server',
      'Credentials are encrypted and stored securely',
    ],
  },
  {
    icon: Bell,
    title: 'Configure TradingView Alert',
    items: [
      'Go to the TradingView tab and configure your signal',
      'Copy the JSON message',
      'Paste as the alert notification webhook body',
      'Set the webhook URL from your Dashboard',
    ],
  },
  {
    icon: SlidersHorizontal,
    title: 'SL/TP Setup',
    items: [
      '"From Ticker" mode: SL/TP values are in pips from entry',
      '"Custom" mode: SL/TP are absolute price levels',
      'Negative size values = percentage of equity',
    ],
  },
  {
    icon: BarChart3,
    title: 'Monitor on Dashboard',
    items: [
      'Check status dots: Bridge, MT5, Broker should all be green',
      'View signal history on the web dashboard',
      'Check the relay log in Settings for debugging',
    ],
  },
];

export function GuidePanel() {
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <ScrollReveal variant="fade-up">
        <h1 className="text-xl font-bold text-fg mb-1">Setup Guide</h1>
        <p className="text-sm text-fg-muted">Get trading in 5 minutes</p>
      </ScrollReveal>

      {steps.map((step, i) => (
        <ScrollReveal key={i} variant="slide-left" delay={0.08 * (i + 1)}>
          <Card>
            <div className="flex items-start gap-4">
              <motion.div
                className="w-10 h-10 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0"
                whileHover={{ scale: 1.15, rotate: 5 }}
                transition={{ type: 'spring', stiffness: 300 }}
              >
                <step.icon size={18} className="text-accent" />
              </motion.div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-bold text-accent">{i + 1}</span>
                  <h3 className="text-sm font-semibold text-fg">{step.title}</h3>
                </div>
                <ul className="space-y-1.5">
                  {step.items.map((item, j) => (
                    <li key={j} className="text-xs text-fg-muted flex items-start gap-2">
                      <span className="text-accent mt-0.5">•</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Card>
        </ScrollReveal>
      ))}

      {/* Links */}
      <ScrollReveal variant="fade-up" delay={0.5}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-3">Useful Links</h3>
          <div className="flex gap-3">
            {[
              { label: 'TradingView', url: 'https://www.tradingview.com' },
              { label: 'MetaTrader 5', url: 'https://www.metatrader5.com' },
            ].map(({ label, url }) => (
              <motion.button
                key={label}
                className="flex items-center gap-1.5 px-3 py-2 rounded-[var(--radius)] bg-bg-hover border border-border text-xs text-fg-muted hover:text-accent cursor-pointer transition-colors"
                onClick={() => bridge.openExternal(url)}
                whileHover={{ scale: 1.03 }}
              >
                <ExternalLink size={12} />
                {label}
              </motion.button>
            ))}
          </div>
        </Card>
      </ScrollReveal>

      {/* SL/TP Disclaimer */}
      <ScrollReveal variant="fade-up" delay={0.55}>
        <Card>
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="text-accent shrink-0 mt-0.5" />
            <div>
              <h3 className="text-sm font-semibold text-fg mb-1">SL/TP Disclaimer</h3>
              <p className="text-xs text-fg-muted leading-relaxed">
                Stop Loss and Take Profit orders are placed as pending orders on MT5. Their execution depends on
                broker conditions, market liquidity, and slippage. PlatAlgo does not guarantee fills at exact
                price levels. Always monitor your positions and use appropriate risk management.
              </p>
            </div>
          </div>
        </Card>
      </ScrollReveal>
    </div>
  );
}
