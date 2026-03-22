import { useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Send, Plus, Trash2, Power, FlaskConical, MessageSquare, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { OutlineButton } from '../ui/OutlineButton';
import { Input } from '../ui/Input';
import { ScrollReveal } from '../ui/ScrollReveal';
import { useAppStore } from '../../store/appStore';
import {
  getTelegramChannels, addTelegramChannel, toggleTelegramChannel,
  deleteTelegramChannel, getTelegramSignals, testTelegramParse,
} from '../../lib/api';

export function TelegramPanel() {
  const auth = useAppStore((s) => s.auth);
  const [chatId, setChatId] = useState('');
  const [riskPct, setRiskPct] = useState('1.0');
  const [maxTrades, setMaxTrades] = useState('10');
  const [allowedSymbols, setAllowedSymbols] = useState('');
  const [testText, setTestText] = useState('');
  const [testResult, setTestResult] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);
  const queryClient = useQueryClient();

  const enabled = !!auth.userId && !!auth.apiKey;

  const { data: channelData, isPending: channelsLoading } = useQuery({
    queryKey: ['telegram-channels', auth.userId],
    queryFn: () => getTelegramChannels(auth.userId!, auth.apiKey!),
    enabled,
  });

  const { data: signalData, isPending: signalsLoading, refetch: refetchSignals } = useQuery({
    queryKey: ['telegram-signals', auth.userId],
    queryFn: () => getTelegramSignals(auth.userId!, auth.apiKey!),
    enabled: enabled && showLog,
  });

  const addMutation = useMutation({
    mutationFn: () => addTelegramChannel(auth.userId!, auth.apiKey!, {
      chat_id: chatId,
      risk_pct: parseFloat(riskPct),
      max_trades_per_day: parseInt(maxTrades),
      allowed_symbols: allowedSymbols || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-channels'] });
      setChatId('');
      toast.success('Channel added');
    },
    onError: () => toast.error('Failed to add channel'),
  });

  const toggleMutation = useMutation({
    mutationFn: (channelId: string) => toggleTelegramChannel(auth.userId!, auth.apiKey!, channelId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['telegram-channels'] }),
    onError: () => toast.error('Failed to toggle channel'),
  });

  const deleteMutation = useMutation({
    mutationFn: (channelId: string) => deleteTelegramChannel(auth.userId!, auth.apiKey!, channelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['telegram-channels'] });
      toast.success('Channel removed');
    },
    onError: () => toast.error('Failed to remove channel'),
  });

  const handleTestParse = async (useLlm: boolean) => {
    if (!testText || !auth.userId || !auth.apiKey) return;
    setTestingLlm(true);
    try {
      const data = await testTelegramParse(auth.userId, auth.apiKey, testText, useLlm);
      setTestResult(JSON.stringify(data, null, 2));
    } catch {
      toast.error('Parse test failed');
    } finally {
      setTestingLlm(false);
    }
  };

  if (!auth.userId) {
    return (
      <div className="max-w-4xl mx-auto flex flex-col items-center justify-center py-24 gap-4">
        <Send size={32} className="text-fg-faint" />
        <p className="text-fg-muted text-sm">Sign in on the Connect tab to manage Telegram signals</p>
      </div>
    );
  }

  const channels = channelData?.channels || [];
  const botRunning = channelData?.bot_running || false;
  const botUsername = channelData?.bot_username || 'PlatalgoSignalBot';
  const signals = signalData?.signals || [];

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <ScrollReveal variant="fade-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-fg mb-2">Telegram Signals</h1>
            <p className="text-sm text-fg-muted">Connect signal channels to auto-execute trades</p>
          </div>
          <div className="flex items-center gap-2">
            {channelsLoading
              ? <Loader2 size={14} className="animate-spin text-fg-muted" />
              : <div className={`w-2.5 h-2.5 rounded-full ${botRunning ? 'bg-success animate-[pulse-dot_2s_ease-in-out_infinite]' : 'bg-danger'}`} />
            }
            <span className="text-xs text-fg-muted">
              {channelsLoading ? 'Loading...' : botRunning ? `@${botUsername} Online` : 'Bot Offline'}
            </span>
          </div>
        </div>
      </ScrollReveal>

      {/* Setup instructions */}
      <ScrollReveal variant="fade-up" delay={0.05}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <Send size={16} className="text-accent" />
            Quick Setup
          </h3>
          <ol className="space-y-2 text-xs text-fg-muted list-decimal list-inside">
            <li>Add <span className="text-accent font-medium">@{botUsername}</span> to your signal channel as admin</li>
            <li>Get the channel's Chat ID (forward a message to <span className="text-accent font-medium">@userinfobot</span>)</li>
            <li>Enter the Chat ID below and configure risk settings</li>
            <li>The bot will parse signals and execute trades automatically</li>
          </ol>
        </Card>
      </ScrollReveal>

      {/* Add channel form */}
      <ScrollReveal variant="fade-up" delay={0.1}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-5 flex items-center gap-2">
            <Plus size={16} className="text-accent" />
            Add Channel
          </h3>
          <div className="grid grid-cols-2 gap-4 mb-5">
            <Input label="Chat ID" value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="-100..." />
            <Input label="Risk %" type="number" value={riskPct} onChange={(e) => setRiskPct(e.target.value)} badge="% of equity" />
            <Input label="Max Trades/Day" type="number" value={maxTrades} onChange={(e) => setMaxTrades(e.target.value)} />
            <Input label="Allowed Symbols" value={allowedSymbols} onChange={(e) => setAllowedSymbols(e.target.value)} placeholder="All (or EURUSD,XAUUSD)" />
          </div>
          <GoldButton onClick={() => addMutation.mutate()} disabled={!chatId || addMutation.isPending} size="sm">
            {addMutation.isPending
              ? <><Loader2 size={14} className="mr-1.5 inline animate-spin" />Adding...</>
              : <><Plus size={14} className="mr-1.5 inline" />Add Channel</>
            }
          </GoldButton>
        </Card>
      </ScrollReveal>

      {/* Channel list */}
      <ScrollReveal variant="fade-up" delay={0.15}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-5">Connected Channels</h3>
          {channelsLoading ? (
            <div className="flex items-center gap-2 text-xs text-fg-muted py-4">
              <Loader2 size={14} className="animate-spin" />
              Loading channels...
            </div>
          ) : channels.length === 0 ? (
            <p className="text-xs text-fg-faint py-2">No channels connected yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-fg-muted text-left border-b border-border">
                    <th className="pb-3 font-medium">Channel</th>
                    <th className="pb-3 font-medium">Chat ID</th>
                    <th className="pb-3 font-medium">Risk %</th>
                    <th className="pb-3 font-medium">Max/Day</th>
                    <th className="pb-3 font-medium">Symbols</th>
                    <th className="pb-3 font-medium">Status</th>
                    <th className="pb-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((ch) => (
                    <tr key={ch.channel_id} className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                      <td className="py-3 text-fg">{ch.chat_title || '—'}</td>
                      <td className="py-3 font-mono text-fg-muted">{ch.chat_id}</td>
                      <td className="py-3 text-accent">{ch.risk_pct}%</td>
                      <td className="py-3">{ch.max_trades_per_day}</td>
                      <td className="py-3 text-fg-muted">{ch.allowed_symbols || 'All'}</td>
                      <td className="py-3">
                        <span className={`inline-flex items-center gap-1 ${ch.enabled ? 'text-success' : 'text-danger'}`}>
                          <div className={`w-1.5 h-1.5 rounded-full ${ch.enabled ? 'bg-success' : 'bg-danger'}`} />
                          {ch.enabled ? 'ON' : 'OFF'}
                        </span>
                      </td>
                      <td className="py-3">
                        <div className="flex gap-1.5">
                          <motion.button
                            className="px-2 py-1 rounded bg-bg-hover border border-border text-fg-muted hover:text-accent cursor-pointer transition-colors text-[0.625rem] disabled:opacity-40"
                            onClick={() => toggleMutation.mutate(ch.channel_id)}
                            disabled={toggleMutation.isPending}
                            whileTap={{ scale: 0.95 }}
                          >
                            <Power size={12} />
                          </motion.button>
                          <motion.button
                            className="px-2 py-1 rounded bg-danger-bg border border-danger/20 text-danger cursor-pointer transition-colors text-[0.625rem] disabled:opacity-40"
                            onClick={() => deleteMutation.mutate(ch.channel_id)}
                            disabled={deleteMutation.isPending}
                            whileTap={{ scale: 0.95 }}
                          >
                            <Trash2 size={12} />
                          </motion.button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </ScrollReveal>

      {/* Signal log */}
      <ScrollReveal variant="fade-up" delay={0.2}>
        <Card>
          <button
            className="flex items-center justify-between w-full text-left cursor-pointer bg-transparent border-none text-fg"
            onClick={() => {
              const next = !showLog;
              setShowLog(next);
              if (next) refetchSignals();
            }}
          >
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <MessageSquare size={16} className="text-accent" />
              Recent Signal Log
            </h3>
            <span className="text-fg-muted text-xs">{showLog ? '▲' : '▼'}</span>
          </button>
          {showLog && (
            <div className="mt-5 space-y-2 max-h-[300px] overflow-y-auto">
              {signalsLoading ? (
                <div className="flex items-center gap-2 text-xs text-fg-muted py-4">
                  <Loader2 size={14} className="animate-spin" />
                  Loading signals...
                </div>
              ) : signals.length === 0 ? (
                <p className="text-xs text-fg-faint">No signals yet</p>
              ) : signals.map((s) => (
                <div key={s.log_id} className="flex items-start gap-3 p-3 rounded-lg bg-bg-input border border-border text-xs">
                  <div className="flex-1">
                    <div className="text-fg-muted truncate max-w-[400px]">{s.raw_text}</div>
                    <div className="mt-1.5 flex gap-3">
                      <span className={s.parsed_action === 'BUY' ? 'text-buy font-medium' : s.parsed_action === 'SELL' ? 'text-sell font-medium' : 'text-fg-faint'}>
                        {s.parsed_action || '—'}
                      </span>
                      <span className="text-fg">{s.parsed_symbol || '—'}</span>
                      <span className="text-fg-muted">conf: {(s.parse_confidence * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  <span className={`text-[0.625rem] px-1.5 py-0.5 rounded ${
                    s.execution_status === 'executed' ? 'bg-success-bg text-success' :
                    s.execution_status === 'failed' ? 'bg-danger-bg text-danger' :
                    'bg-bg-hover text-fg-muted'
                  }`}>
                    {s.execution_status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </ScrollReveal>

      {/* Test parser */}
      <ScrollReveal variant="fade-up" delay={0.25}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <FlaskConical size={16} className="text-accent" />
            Test Parser
          </h3>
          <textarea
            className="w-full bg-bg-input border border-border text-fg text-xs px-3 py-3 rounded-[var(--radius)] outline-none resize-none h-20 font-mono focus:border-accent/50 focus:shadow-[0_0_0_3px_var(--color-accent-muted)] transition-all"
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
            placeholder="Paste a sample signal message..."
          />
          <div className="flex gap-2 mt-3">
            <OutlineButton size="sm" onClick={() => handleTestParse(false)} disabled={testingLlm || !testText}>
              Regex Parse
            </OutlineButton>
            <OutlineButton size="sm" onClick={() => handleTestParse(true)} disabled={testingLlm || !testText}>
              {testingLlm ? <><Loader2 size={12} className="animate-spin mr-1 inline" />Parsing...</> : 'LLM Parse'}
            </OutlineButton>
          </div>
          {testResult && (
            <pre className="mt-4 bg-bg-input border border-border rounded-[var(--radius)] p-3 text-[0.625rem] text-fg-soft font-mono overflow-x-auto whitespace-pre">
              {testResult}
            </pre>
          )}
        </Card>
      </ScrollReveal>
    </div>
  );
}
