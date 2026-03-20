import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Send, Plus, Trash2, Power, FlaskConical, MessageSquare } from 'lucide-react';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { OutlineButton } from '../ui/OutlineButton';
import { Input } from '../ui/Input';
import { ScrollReveal } from '../ui/ScrollReveal';
import { BRIDGE_URL } from '../../lib/constants';

interface Channel {
  channel_id: string;
  chat_id: string;
  chat_title: string;
  enabled: number;
  risk_pct: number;
  max_trades_per_day: number;
  allowed_symbols: string | null;
}

interface SignalLog {
  log_id: string;
  raw_text: string;
  parsed_action: string;
  parsed_symbol: string;
  parse_confidence: number;
  execution_status: string;
  created_at: number;
}

export function TelegramPanel() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [signals, setSignals] = useState<SignalLog[]>([]);
  const [botRunning, setBotRunning] = useState(false);
  const [botUsername, setBotUsername] = useState('');
  const [chatId, setChatId] = useState('');
  const [riskPct, setRiskPct] = useState('1.0');
  const [maxTrades, setMaxTrades] = useState('10');
  const [allowedSymbols, setAllowedSymbols] = useState('');
  const [testText, setTestText] = useState('');
  const [testResult, setTestResult] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  const loadChannels = async () => {
    try {
      const res = await fetch(`${BRIDGE_URL}/api/telegram/channels`, { credentials: 'include' });
      const data = await res.json();
      setChannels(data.channels || []);
      setBotRunning(data.bot_running || false);
      setBotUsername(data.bot_username || '');
    } catch { /* ignore */ }
  };

  const loadSignals = async () => {
    try {
      const res = await fetch(`${BRIDGE_URL}/api/telegram/signals?limit=30`, { credentials: 'include' });
      const data = await res.json();
      setSignals(data.signals || []);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadChannels(); }, []);

  const addChannel = async () => {
    if (!chatId) return;
    try {
      await fetch(`${BRIDGE_URL}/api/telegram/channels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          chat_id: chatId,
          risk_pct: parseFloat(riskPct),
          max_trades_per_day: parseInt(maxTrades),
          allowed_symbols: allowedSymbols || null,
          script_name: 'Telegram',
        }),
      });
      setChatId('');
      loadChannels();
    } catch { /* ignore */ }
  };

  const toggleChannel = async (id: string) => {
    await fetch(`${BRIDGE_URL}/api/telegram/channels/${id}/toggle`, {
      method: 'POST', credentials: 'include',
    });
    loadChannels();
  };

  const deleteChannel = async (id: string) => {
    await fetch(`${BRIDGE_URL}/api/telegram/channels/${id}`, {
      method: 'DELETE', credentials: 'include',
    });
    loadChannels();
  };

  const testParse = async (useLlm: boolean) => {
    if (!testText) return;
    const res = await fetch(`${BRIDGE_URL}/api/telegram/test-parse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ text: testText, use_llm: useLlm }),
    });
    const data = await res.json();
    setTestResult(JSON.stringify(data, null, 2));
  };

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <ScrollReveal variant="fade-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-fg mb-1">Telegram Signals</h1>
            <p className="text-sm text-fg-muted">Connect signal channels to auto-execute trades</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`w-2.5 h-2.5 rounded-full ${botRunning ? 'bg-success animate-[pulse-dot_2s_ease-in-out_infinite]' : 'bg-danger'}`} />
            <span className="text-xs text-fg-muted">{botRunning ? `@${botUsername} Online` : 'Bot Offline'}</span>
          </div>
        </div>
      </ScrollReveal>

      {/* Setup instructions */}
      <ScrollReveal variant="fade-up" delay={0.05}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-3 flex items-center gap-2">
            <Send size={16} className="text-accent" />
            Quick Setup
          </h3>
          <ol className="space-y-1.5 text-xs text-fg-muted list-decimal list-inside">
            <li>Add <span className="text-accent font-medium">@{botUsername || 'PlatalgoSignalBot'}</span> to your signal channel as admin</li>
            <li>Get the channel's Chat ID (forward a message to <span className="text-accent font-medium">@userinfobot</span>)</li>
            <li>Enter the Chat ID below and configure risk settings</li>
            <li>The bot will parse signals and execute trades automatically</li>
          </ol>
        </Card>
      </ScrollReveal>

      {/* Add channel form */}
      <ScrollReveal variant="fade-up" delay={0.1}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
            <Plus size={16} className="text-accent" />
            Add Channel
          </h3>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <Input label="Chat ID" value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="-100..." />
            <Input label="Risk %" type="number" value={riskPct} onChange={(e) => setRiskPct(e.target.value)} badge="% of equity" />
            <Input label="Max Trades/Day" type="number" value={maxTrades} onChange={(e) => setMaxTrades(e.target.value)} />
            <Input label="Allowed Symbols" value={allowedSymbols} onChange={(e) => setAllowedSymbols(e.target.value)} placeholder="All (or EURUSD,XAUUSD)" />
          </div>
          <GoldButton onClick={addChannel} disabled={!chatId} size="sm">
            <Plus size={14} className="mr-1.5 inline" />
            Add Channel
          </GoldButton>
        </Card>
      </ScrollReveal>

      {/* Channel list */}
      <ScrollReveal variant="fade-up" delay={0.15}>
        <Card>
          <h3 className="text-sm font-semibold text-fg mb-4">Connected Channels</h3>
          {channels.length === 0 ? (
            <p className="text-xs text-fg-faint">No channels connected yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-fg-muted text-left border-b border-border">
                    <th className="pb-2 font-medium">Channel</th>
                    <th className="pb-2 font-medium">Chat ID</th>
                    <th className="pb-2 font-medium">Risk %</th>
                    <th className="pb-2 font-medium">Max/Day</th>
                    <th className="pb-2 font-medium">Symbols</th>
                    <th className="pb-2 font-medium">Status</th>
                    <th className="pb-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((ch) => (
                    <tr key={ch.channel_id} className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                      <td className="py-2.5 text-fg">{ch.chat_title || '—'}</td>
                      <td className="py-2.5 font-mono text-fg-muted">{ch.chat_id}</td>
                      <td className="py-2.5 text-accent">{ch.risk_pct}%</td>
                      <td className="py-2.5">{ch.max_trades_per_day}</td>
                      <td className="py-2.5 text-fg-muted">{ch.allowed_symbols || 'All'}</td>
                      <td className="py-2.5">
                        <span className={`inline-flex items-center gap-1 ${ch.enabled ? 'text-success' : 'text-danger'}`}>
                          <div className={`w-1.5 h-1.5 rounded-full ${ch.enabled ? 'bg-success' : 'bg-danger'}`} />
                          {ch.enabled ? 'ON' : 'OFF'}
                        </span>
                      </td>
                      <td className="py-2.5">
                        <div className="flex gap-1.5">
                          <motion.button
                            className="px-2 py-1 rounded bg-bg-hover border border-border text-fg-muted hover:text-accent cursor-pointer transition-colors text-[0.625rem]"
                            onClick={() => toggleChannel(ch.channel_id)}
                            whileTap={{ scale: 0.95 }}
                          >
                            <Power size={12} />
                          </motion.button>
                          <motion.button
                            className="px-2 py-1 rounded bg-danger-bg border border-danger/20 text-danger hover:text-danger cursor-pointer transition-colors text-[0.625rem]"
                            onClick={() => deleteChannel(ch.channel_id)}
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
            onClick={() => { setShowLog(!showLog); if (!showLog) loadSignals(); }}
          >
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <MessageSquare size={16} className="text-accent" />
              Recent Signal Log
            </h3>
            <span className="text-fg-muted text-xs">{showLog ? '▲' : '▼'}</span>
          </button>
          {showLog && (
            <div className="mt-4 space-y-2 max-h-[300px] overflow-y-auto">
              {signals.length === 0 ? (
                <p className="text-xs text-fg-faint">No signals yet</p>
              ) : signals.map((s) => (
                <div key={s.log_id} className="flex items-start gap-3 p-2.5 rounded-lg bg-bg-input border border-border text-xs">
                  <div className="flex-1">
                    <div className="text-fg-muted truncate max-w-[400px]">{s.raw_text}</div>
                    <div className="mt-1 flex gap-3">
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
          <h3 className="text-sm font-semibold text-fg mb-3 flex items-center gap-2">
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
            <OutlineButton size="sm" onClick={() => testParse(false)}>Regex Parse</OutlineButton>
            <OutlineButton size="sm" onClick={() => testParse(true)}>LLM Parse</OutlineButton>
          </div>
          {testResult && (
            <pre className="mt-3 bg-bg-input border border-border rounded-[var(--radius)] p-3 text-[0.625rem] text-fg-soft font-mono overflow-x-auto whitespace-pre">
              {testResult}
            </pre>
          )}
        </Card>
      </ScrollReveal>
    </div>
  );
}
