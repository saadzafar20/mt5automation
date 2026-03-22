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
    title: 'Create an Account & Sign In',
    items: [
      'No account yet? Register at app.platalgo.com',
      'Use Google or Facebook for quick access (recommended)',
      'Or enter your email and password',
      'Enable "Remember me" to auto-connect on startup',
    ],
  },
  {
    icon: Server,
    title: 'Enable Cloud Execution (VPS Mode)',
    items: [
      'VPS Mode (recommended): 24/7 execution on the cloud — no PC required',
      'Enter your MT5 credentials and click "Login to MT5 for 24/7 VPS Mode"',
      'Your credentials are encrypted and stored securely on the server',
    ],
  },
  {
    icon: KeyRound,
    title: 'Get Your Webhook URL',
    items: [
      'Navigate to the Dashboard tab after signing in',
      'Copy your unique Webhook URL',
      'This is the URL you paste into TradingView alert webhooks',
    ],
  },
  {
    icon: Bell,
    title: 'Configure TradingView Alert',
    items: [
      'Go to the TradingView tab and configure your signal',
      'Copy the JSON message',
      'In TradingView: create an alert → Notifications → Webhook URL',
      'Paste your Webhook URL and the JSON message body',
    ],
  },
  {
    icon: SlidersHorizontal,
    title: 'SL/TP Setup',
    items: [
      'SL/TP values in the JSON are in pips from entry price',
      'Negative size values = percentage of account equity (e.g. -1 = 1%)',
      'Use the TradingView tab to build and preview your message',
    ],
  },
  {
    icon: BarChart3,
    title: 'Monitor on Dashboard',
    items: [
      'Check status dots: Bridge, MT5, Broker should all be green',
      'View signal history and execution stats on the Dashboard tab',
      'Check the relay log in Settings for debugging',
    ],
  },
];

export function GuidePanel() {
  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <ScrollReveal variant="fade-up">
        <h1 className="text-2xl font-bold text-fg mb-2">Setup Guide</h1>
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
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-xs font-bold text-accent">{i + 1}</span>
                  <h3 className="text-sm font-semibold text-fg">{step.title}</h3>
                </div>
                <ul className="space-y-2">
                  {step.items.map((item, j) => (
                    <li key={j} className="text-xs text-fg-muted flex items-start gap-2">
                      <span className="text-accent mt-0.5 shrink-0">•</span>
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
          <h3 className="text-sm font-semibold text-fg mb-4">Useful Links</h3>
          <div className="flex flex-wrap gap-3">
            {[
              { label: 'Register / Sign Up', url: 'https://app.platalgo.com/register' },
              { label: 'Web Dashboard', url: 'https://app.platalgo.com/dashboard' },
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
              <h3 className="text-sm font-semibold text-fg mb-2">Risk Disclaimer</h3>
              <p className="text-xs text-fg-muted leading-relaxed">
                Stop Loss and Take Profit orders are placed as pending orders on MT5. Their execution depends on
                broker conditions, market liquidity, and slippage. PlatAlgo does not guarantee fills at exact
                price levels. Always monitor your positions and use appropriate risk management.
                Past performance does not guarantee future results.
              </p>
            </div>
          </div>
        </Card>
      </ScrollReveal>
    </div>
  );
}
