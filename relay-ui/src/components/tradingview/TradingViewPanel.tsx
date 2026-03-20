import { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { Copy, RotateCcw } from 'lucide-react';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { OutlineButton } from '../ui/OutlineButton';
import { Input } from '../ui/Input';
import { ScrollReveal } from '../ui/ScrollReveal';
import { bridge } from '../../lib/bridge';

export function TradingViewPanel() {
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY');
  const [symbol, setSymbol] = useState('{{ticker}}');
  const [size, setSize] = useState('1');
  const [sl, setSl] = useState('');
  const [tp, setTp] = useState('');
  const [scriptName, setScriptName] = useState('');
  const [copied, setCopied] = useState(false);

  const jsonMessage = useMemo(() => {
    const msg: Record<string, unknown> = {
      action,
      symbol,
      size: `-${size}`,
    };
    if (sl) msg.sl = sl;
    if (tp) msg.tp = tp;
    if (scriptName) msg.script_name = scriptName;
    return JSON.stringify(msg, null, 2);
  }, [action, symbol, size, sl, tp, scriptName]);

  const handleCopy = () => {
    bridge.setClipboard(jsonMessage);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleReset = () => {
    setAction('BUY');
    setSymbol('{{ticker}}');
    setSize('1');
    setSl('');
    setTp('');
    setScriptName('');
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <ScrollReveal variant="fade-up">
        <h1 className="text-xl font-bold text-fg mb-1">TradingView</h1>
        <p className="text-sm text-fg-muted">Generate webhook alert messages</p>
      </ScrollReveal>

      {/* Action Toggle */}
      <ScrollReveal variant="scale" delay={0.1}>
        <Card hover={false}>
          <div className="flex gap-2 mb-5">
            <motion.button
              className={`flex-1 py-3 rounded-[var(--radius)] font-semibold text-sm cursor-pointer transition-all duration-200 border-none
                ${action === 'BUY' ? 'bg-buy text-white shadow-[0_0_20px_var(--color-buy)/30]' : 'bg-bg-hover text-fg-muted'}`}
              onClick={() => setAction('BUY')}
              whileTap={{ scale: 0.98 }}
            >
              BUY
            </motion.button>
            <motion.button
              className={`flex-1 py-3 rounded-[var(--radius)] font-semibold text-sm cursor-pointer transition-all duration-200 border-none
                ${action === 'SELL' ? 'bg-sell text-white shadow-[0_0_20px_var(--color-sell)/30]' : 'bg-bg-hover text-fg-muted'}`}
              onClick={() => setAction('SELL')}
              whileTap={{ scale: 0.98 }}
            >
              SELL
            </motion.button>
          </div>

          {/* Fields */}
          <div className="grid grid-cols-2 gap-4 mb-5">
            <Input label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
            <Input label="Size as % Equity" type="number" value={size} onChange={(e) => setSize(e.target.value)} badge="%" />
            <Input label="Stop Loss (pips)" type="number" value={sl} onChange={(e) => setSl(e.target.value)} placeholder="Optional" />
            <Input label="Take Profit (pips)" type="number" value={tp} onChange={(e) => setTp(e.target.value)} placeholder="Optional" />
          </div>
          <Input label="Script Name" value={scriptName} onChange={(e) => setScriptName(e.target.value)} placeholder="Optional — group signals" />
        </Card>
      </ScrollReveal>

      {/* JSON Preview */}
      <ScrollReveal variant="fade-up" delay={0.15}>
        <Card>
          <h3 className="text-xs font-semibold text-fg-muted mb-3">Message Preview</h3>
          <pre className="bg-bg-input border border-border rounded-[var(--radius)] p-4 text-xs text-fg-soft font-mono overflow-x-auto whitespace-pre leading-relaxed">
            {jsonMessage}
          </pre>
          <div className="flex gap-2 mt-4">
            <GoldButton onClick={handleCopy}>
              <Copy size={14} className="mr-1.5 inline" />
              {copied ? 'Copied!' : 'Copy Message'}
            </GoldButton>
            <OutlineButton onClick={handleReset}>
              <RotateCcw size={14} className="mr-1.5 inline" />
              Reset
            </OutlineButton>
          </div>
        </Card>
      </ScrollReveal>
    </div>
  );
}
