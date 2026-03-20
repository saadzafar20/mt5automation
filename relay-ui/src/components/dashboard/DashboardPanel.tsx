import { useState, useCallback } from 'react';
import { RefreshCw, ExternalLink, Copy, Eye, EyeOff } from 'lucide-react';
import { motion } from 'framer-motion';
import { useAppStore } from '../../store/appStore';
import { getDashboardSummary } from '../../lib/api';
import { bridge } from '../../lib/bridge';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { StatusRing } from '../ui/StatusRing';
import { ScrollReveal } from '../ui/ScrollReveal';

export function DashboardPanel() {
  const auth = useAppStore((s) => s.auth);
  const dots = useAppStore((s) => s.relayDots);
  const dashboardData = useAppStore((s) => s.dashboardData);
  const setDashboardData = useAppStore((s) => s.setDashboardData);
  const [loading, setLoading] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState('');

  const refresh = useCallback(async () => {
    if (!auth.userId || !auth.apiKey) return;
    setLoading(true);
    try {
      const data = await getDashboardSummary(auth.userId, auth.apiKey);
      setDashboardData({
        webhookUrl: data.webhook_url || '',
        apiKey: data.api_key || auth.apiKey || '',
        relayOnline: data.dashboard?.relay_online || 0,
        relayTotal: data.dashboard?.relay_total || 0,
        scripts: data.dashboard?.scripts || [],
      });
    } finally {
      setLoading(false);
    }
  }, [auth, setDashboardData]);

  const copyText = (text: string, label: string) => {
    bridge.setClipboard(text);
    setCopied(label);
    setTimeout(() => setCopied(''), 2000);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <ScrollReveal variant="fade-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-fg mb-1">Dashboard</h1>
            <p className="text-sm text-fg-muted">Monitor your trading infrastructure</p>
          </div>
          <div className="flex gap-2">
            <OutlineButton size="sm" onClick={refresh} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              <span className="ml-1.5">Refresh</span>
            </OutlineButton>
            <OutlineButton size="sm" onClick={() => bridge.openExternal('https://app.platalgo.com/dashboard')}>
              <ExternalLink size={14} />
              <span className="ml-1.5">Web Dashboard</span>
            </OutlineButton>
          </div>
        </div>
      </ScrollReveal>

      {/* Status Rings */}
      <ScrollReveal variant="scale" delay={0.1}>
        <Card hover={false}>
          <div className="flex justify-around py-4">
            <StatusRing label="Bridge" letter="B" description="Cloud server" status={dots.bridge} />
            <StatusRing label="MT5" letter="M" description="MT5 terminal" status={dots.mt5} />
            <StatusRing label="Broker" letter="K" description="Broker server" status={dots.broker} />
          </div>
        </Card>
      </ScrollReveal>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Webhook URL */}
        <ScrollReveal variant="slide-left" delay={0.15}>
          <Card gold>
            <h3 className="text-xs font-semibold text-fg-muted mb-2 flex items-center gap-2">
              Webhook URL
              <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                PASTE INTO TRADINGVIEW
              </span>
            </h3>
            <div className="flex gap-2">
              <input
                readOnly
                value={dashboardData?.webhookUrl || 'Sign in to see URL'}
                className="flex-1 bg-bg-input border border-border text-fg text-xs px-3 py-2 rounded-[var(--radius)] outline-none font-mono"
              />
              <motion.button
                className="px-3 py-2 rounded-[var(--radius)] bg-bg-hover border border-border text-fg-muted hover:text-accent cursor-pointer transition-colors"
                onClick={() => dashboardData?.webhookUrl && copyText(dashboardData.webhookUrl, 'webhook')}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
              >
                <Copy size={14} className={copied === 'webhook' ? 'text-success' : ''} />
              </motion.button>
            </div>
          </Card>
        </ScrollReveal>

        {/* API Key */}
        <ScrollReveal variant="slide-right" delay={0.15}>
          <Card>
            <h3 className="text-xs font-semibold text-fg-muted mb-2 flex items-center gap-2">
              API Key
              <span className="text-[0.5rem] font-bold text-danger bg-danger-bg px-1.5 py-0.5 rounded">
                KEEP SECRET
              </span>
            </h3>
            <div className="flex gap-2">
              <input
                readOnly
                type={showKey ? 'text' : 'password'}
                value={dashboardData?.apiKey || auth.apiKey || ''}
                className="flex-1 bg-bg-input border border-border text-fg text-xs px-3 py-2 rounded-[var(--radius)] outline-none font-mono"
              />
              <motion.button
                className="px-3 py-2 rounded-[var(--radius)] bg-bg-hover border border-border text-fg-muted hover:text-fg cursor-pointer transition-colors"
                onClick={() => setShowKey(!showKey)}
                whileHover={{ scale: 1.05 }}
              >
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </motion.button>
              <motion.button
                className="px-3 py-2 rounded-[var(--radius)] bg-bg-hover border border-border text-fg-muted hover:text-accent cursor-pointer transition-colors"
                onClick={() => dashboardData?.apiKey && copyText(dashboardData.apiKey, 'api')}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
              >
                <Copy size={14} className={copied === 'api' ? 'text-success' : ''} />
              </motion.button>
            </div>
          </Card>
        </ScrollReveal>
      </div>

      {/* Account Summary */}
      <ScrollReveal variant="fade-up" delay={0.2}>
        <Card>
          <h3 className="text-xs font-semibold text-fg-muted mb-3">Account Summary</h3>
          <div className="bg-bg-input border border-border rounded-[var(--radius)] p-4 font-mono text-xs text-fg-muted space-y-1">
            <div>Account: <span className="text-fg">{auth.userId || '—'}</span></div>
            <div>Relays Online: <span className="text-success">{dashboardData?.relayOnline || 0}</span> / {dashboardData?.relayTotal || 0}</div>
            <div>Scripts: <span className="text-fg">{dashboardData?.scripts.length || 0}</span></div>
            {dashboardData?.scripts.map((s) => (
              <div key={s.script_code} className="pl-4">
                {s.script_name}: <span className="text-accent">{s.signals_count}</span> signals, <span className="text-success">{s.executed_count}</span> executed
              </div>
            ))}
          </div>
        </Card>
      </ScrollReveal>
    </div>
  );
}
