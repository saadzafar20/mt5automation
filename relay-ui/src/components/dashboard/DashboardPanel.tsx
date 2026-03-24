import { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, ExternalLink, Copy, Check, Eye, EyeOff } from 'lucide-react';
import { motion } from 'framer-motion';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip,
  ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { useAppStore } from '../../store/appStore';
import { getDashboardSummary } from '../../lib/api';
import { bridge } from '../../lib/bridge';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { StatusRing } from '../ui/StatusRing';
import { ScrollReveal } from '../ui/ScrollReveal';

interface DashboardResult {
  webhookUrl: string;
  apiKey: string;
  relayOnline: number;
  relayTotal: number;
  scripts: Array<{ script_code: string; script_name: string; signals_count: number; executed_count: number }>;
  managedConnected?: boolean;
  brokerConnected?: boolean;
  circuitBroken?: boolean;
  magicNumber?: number;
  plan?: string;
}

export function DashboardPanel() {
  const auth = useAppStore((s) => s.auth);
  const dots = useAppStore((s) => s.relayDots);
  const setDashboardData = useAppStore((s) => s.setDashboardData);
  const setRelayDots = useAppStore((s) => s.setRelayDots);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState('');
  const queryClient = useQueryClient();

  const { data, isPending, isError, error } = useQuery<DashboardResult>({
    queryKey: ['dashboard', auth.userId],
    queryFn: async () => {
      if (!auth.userId || !auth.apiKey) throw new Error('Not authenticated');
      const res = await getDashboardSummary(auth.userId, auth.apiKey);
      if (!res || res.error) throw new Error(res?.error || 'Failed to load dashboard');
      const result: DashboardResult = {
        webhookUrl: res.webhook_url || '',
        apiKey: res.api_key || auth.apiKey || '',
        relayOnline: res.dashboard?.relay_online || 0,
        relayTotal: res.dashboard?.relay_total || 0,
        scripts: res.dashboard?.scripts || [],
        managedConnected: res.managed_connected ?? false,
        brokerConnected: res.broker_connected ?? false,
        circuitBroken: res.circuit_broken ?? false,
        magicNumber: res.magic_number,
        plan: res.plan,
      };
      // Update relay dots from summary response
      setRelayDots({
        bridge: 'online',
        mt5: result.managedConnected ? 'online' : (result.relayOnline > 0 ? 'online' : 'offline'),
        broker: result.brokerConnected ? 'online' : 'offline',
      });
      // Keep Zustand store in sync for the background polling
      setDashboardData(result);
      return result;
    },
    enabled: !!auth.userId && !!auth.apiKey,
    refetchInterval: 15000,
  });

  // 15-second polling effect for relay status refresh
  useEffect(() => {
    if (!auth.userId || !auth.apiKey) return;
    const interval = setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ['dashboard', auth.userId] });
    }, 15000);
    return () => clearInterval(interval);
  }, [auth.userId, auth.apiKey, queryClient]);

  const copyText = (text: string, label: string) => {
    bridge.setClipboard(text);
    setCopied(label);
    setTimeout(() => setCopied(''), 3000);
  };

  if (!auth.userId) {
    return (
      <div className="max-w-4xl mx-auto flex flex-col items-center justify-center py-24 gap-4">
        <p className="text-fg-muted text-sm">Sign in on the Connect tab to view your dashboard</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <ScrollReveal variant="fade-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-fg mb-2">Dashboard</h1>
            <p className="text-sm text-fg-muted">Monitor your trading infrastructure</p>
          </div>
          <div className="flex gap-2">
            <OutlineButton size="sm" onClick={() => queryClient.invalidateQueries({ queryKey: ['dashboard'] })} disabled={isPending}>
              <RefreshCw size={14} className={isPending ? 'animate-spin' : ''} />
              <span className="ml-1.5">Refresh</span>
            </OutlineButton>
            <OutlineButton size="sm" onClick={() => bridge.openExternal('https://app.platalgo.com/dashboard')}>
              <ExternalLink size={14} />
              <span className="ml-1.5">Web Dashboard</span>
            </OutlineButton>
          </div>
        </div>
      </ScrollReveal>

      {isError && (
        <div className="text-xs text-danger bg-danger-bg px-4 py-3 rounded-lg border border-danger/20">
          {(error as Error)?.message || 'Failed to load dashboard — check your connection'}
        </div>
      )}

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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-7">
        {/* Webhook URL */}
        <ScrollReveal variant="slide-left" delay={0.15}>
          <Card gold>
            <h3 className="text-xs font-semibold text-fg-muted mb-3 flex items-center gap-2">
              Webhook URL
              <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                PASTE INTO TRADINGVIEW
              </span>
            </h3>
            <div className="flex gap-2">
              <input
                readOnly
                value={isPending ? 'Loading...' : (data?.webhookUrl || '—')}
                className="flex-1 bg-bg-input border border-border text-fg text-xs px-3 py-2 rounded-[var(--radius)] outline-none" style={{ fontFamily: 'var(--font-mono)' }}
              />
              <motion.button
                className={`px-3 py-2 rounded-[var(--radius)] border cursor-pointer transition-all duration-200 ${copied === 'webhook' ? 'bg-success-bg border-success/20 text-success' : 'bg-bg-hover border-border text-fg-muted hover:text-accent'}`}
                onClick={() => data?.webhookUrl && copyText(data.webhookUrl, 'webhook')}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                title={copied === 'webhook' ? 'Copied!' : 'Copy'}
              >
                {copied === 'webhook' ? <Check size={14} /> : <Copy size={14} />}
              </motion.button>
            </div>
          </Card>
        </ScrollReveal>

        {/* API Key */}
        <ScrollReveal variant="slide-right" delay={0.15}>
          <Card>
            <h3 className="text-xs font-semibold text-fg-muted mb-3 flex items-center gap-2">
              API Key
              <span className="text-[0.5rem] font-bold text-danger bg-danger-bg px-1.5 py-0.5 rounded">
                KEEP SECRET
              </span>
            </h3>
            <div className="flex gap-2">
              <input
                readOnly
                type={showKey ? 'text' : 'password'}
                value={isPending ? '' : (data?.apiKey || auth.apiKey || '')}
                className="flex-1 bg-bg-input border border-border text-fg text-xs px-3 py-2 rounded-[var(--radius)] outline-none" style={{ fontFamily: 'var(--font-mono)' }}
              />
              <motion.button
                className="px-3 py-2 rounded-[var(--radius)] bg-bg-hover border border-border text-fg-muted hover:text-fg cursor-pointer transition-colors"
                onClick={() => setShowKey(!showKey)}
                whileHover={{ scale: 1.05 }}
              >
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </motion.button>
              <motion.button
                className={`px-3 py-2 rounded-[var(--radius)] border cursor-pointer transition-all duration-200 ${copied === 'api' ? 'bg-success-bg border-success/20 text-success' : 'bg-bg-hover border-border text-fg-muted hover:text-accent'}`}
                onClick={() => data?.apiKey && copyText(data.apiKey, 'api')}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                title={copied === 'api' ? 'Copied!' : 'Copy'}
              >
                {copied === 'api' ? <Check size={14} /> : <Copy size={14} />}
              </motion.button>
            </div>
          </Card>
        </ScrollReveal>
      </div>

      {/* Account Summary */}
      <ScrollReveal variant="fade-up" delay={0.2}>
        <Card>
          <h3 className="text-xs font-semibold text-fg-muted mb-4">Account Summary</h3>
          <div className="bg-bg-input border border-border rounded-[var(--radius)] p-4 text-xs text-fg-muted space-y-2" style={{ fontFamily: 'var(--font-mono)' }}>
            <div>Account: <span className="text-fg">{auth.userId || '—'}</span></div>
            <div>Relays Online: <span className="text-success">{data?.relayOnline || 0}</span> / {data?.relayTotal || 0}</div>
            <div>Scripts: <span className="text-fg">{data?.scripts.length || 0}</span></div>
            {data?.scripts.map((s) => (
              <div key={s.script_code} className="pl-4">
                {s.script_name}: <span className="text-accent">{s.signals_count}</span> signals,{' '}
                <span className="text-success">{s.executed_count}</span> executed
              </div>
            ))}
          </div>
        </Card>
      </ScrollReveal>

      {/* Signal Execution Chart */}
      {data && data.scripts.length > 0 && (
        <ScrollReveal variant="fade-up" delay={0.25}>
          <Card hover={false}>
            <h3 className="text-xs font-semibold text-fg-muted mb-5">Signal Execution by Script</h3>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={data.scripts} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
                <XAxis
                  dataKey="script_name"
                  tick={{ fill: 'var(--color-fg-muted)', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: 'var(--color-fg-muted)', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <RechartsTooltip
                  contentStyle={{
                    background: 'var(--color-bg-card)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 'var(--radius)',
                    fontSize: '0.75rem',
                    color: 'var(--color-fg)',
                  }}
                  cursor={{ fill: 'var(--color-bg-hover)' }}
                />
                <Bar dataKey="signals_count" name="Signals" fill="var(--color-accent)" radius={[4, 4, 0, 0]} />
                <Bar dataKey="executed_count" name="Executed" fill="var(--color-success)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="flex items-center gap-5 mt-3 px-1">
              <span className="flex items-center gap-1.5 text-xs text-fg-muted">
                <span className="w-2.5 h-2.5 rounded-sm bg-accent inline-block" />
                Signals received
              </span>
              <span className="flex items-center gap-1.5 text-xs text-fg-muted">
                <span className="w-2.5 h-2.5 rounded-sm bg-success inline-block" />
                Trades executed
              </span>
            </div>
          </Card>
        </ScrollReveal>
      )}
    </div>
  );
}
