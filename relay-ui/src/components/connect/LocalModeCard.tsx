import { useState } from 'react';
import { Monitor, Check, X } from 'lucide-react';
import { Card } from '../ui/Card';
import { OutlineButton } from '../ui/OutlineButton';
import { useAppStore } from '../../store/appStore';
import { relayStart, relayStop } from '../../lib/api';

const features = [
  { text: 'Full control over MT5', ok: true },
  { text: 'Uses your own machine', ok: true },
  { text: 'Requires PC always on', ok: false },
  { text: 'Windows only', ok: false },
];

export function LocalModeCard() {
  const auth = useAppStore((s) => s.auth);
  const relayStatus = useAppStore((s) => s.relayStatus);
  const [loading, setLoading] = useState(false);
  const isRunning = relayStatus !== 'Idle' && relayStatus !== 'Offline';

  const handleStart = async () => {
    if (!auth.userId) return;
    setLoading(true);
    try {
      await relayStart({ user_id: auth.userId, api_key: auth.apiKey || undefined, relay_type: 'self-hosted' });
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      await relayStop();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center justify-between mb-5 gap-2">
        <h2 className="text-base font-semibold text-fg flex items-center gap-2">
          <Monitor size={18} className="text-fg-soft" />
          Local Mode
        </h2>
        <span className="text-[0.5rem] font-bold text-fg-muted bg-bg-hover px-2 py-0.5 rounded-full uppercase tracking-wider shrink-0">
          Windows
        </span>
      </div>

      <div className="space-y-3 mb-6">
        {features.map(({ text, ok }, i) => (
          <div key={i} className="flex items-center gap-2 text-xs text-fg-muted">
            {ok ? (
              <Check size={14} className="text-success transition-all duration-300 hover:scale-125" />
            ) : (
              <X size={14} className="text-danger transition-all duration-300 hover:scale-125" />
            )}
            {text}
          </div>
        ))}
      </div>

      {isRunning ? (
        <OutlineButton danger fullWidth onClick={handleStop} disabled={loading}>
          Stop
        </OutlineButton>
      ) : (
        <OutlineButton fullWidth onClick={handleStart} disabled={loading || !auth.userId}>
          {loading ? 'Starting...' : 'Select Local Mode'}
        </OutlineButton>
      )}
    </Card>
  );
}
