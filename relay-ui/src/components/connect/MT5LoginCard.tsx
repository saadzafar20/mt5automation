import { useState } from 'react';
import { Lock, Eye, EyeOff, Cloud } from 'lucide-react';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { Input } from '../ui/Input';
import { useAppStore } from '../../store/appStore';
import { managedEnable } from '../../lib/api';

export function MT5LoginCard() {
  const [mt5Login, setMt5Login] = useState('');
  const [mt5Password, setMt5Password] = useState('');
  const [mt5Server, setMt5Server] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const auth = useAppStore((s) => s.auth);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const vpsActive = useAppStore((s) => s.vpsActive);

  const handleCloudLogin = async () => {
    if (!mt5Login || !mt5Password || !mt5Server || !auth.userId) return;
    setLoading(true);
    setError('');
    try {
      const result = await managedEnable({
        user_id: auth.userId,
        api_key: auth.apiKey || undefined,
        mt5_login: mt5Login,
        mt5_password: mt5Password,
        mt5_server: mt5Server,
      });
      if (result.error) {
        setError(result.error);
      } else {
        setVpsActive(true);
      }
    } catch {
      setError('Connection failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-2 mb-6">
        <Lock size={18} className="text-danger" />
        <h2 className="text-base font-semibold text-fg">MT5 Broker Login</h2>
        <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">OPTIONAL</span>
      </div>

      <div className="space-y-5">
        <Input
          label="Account Number"
          value={mt5Login}
          onChange={(e) => setMt5Login(e.target.value)}
          placeholder="12345678"
        />
        <div className="relative">
          <Input
            label="MT5 Password"
            type={showPw ? 'text' : 'password'}
            value={mt5Password}
            onChange={(e) => setMt5Password(e.target.value)}
            placeholder="••••••••"
          />
          <button
            className="absolute right-3 top-[calc(50%+4px)] text-fg-muted hover:text-fg transition-colors cursor-pointer bg-transparent border-none"
            onClick={() => setShowPw(!showPw)}
          >
            {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <Input
          label="Broker Server"
          value={mt5Server}
          onChange={(e) => setMt5Server(e.target.value)}
          placeholder="ICMarkets-Live01"
        />

        {error && <div className="text-xs text-danger bg-danger-bg px-3 py-2 rounded-lg">{error}</div>}

        {vpsActive ? (
          <div className="flex items-center gap-2 px-4 py-3 rounded-[var(--radius)] bg-success-bg border border-success/20 text-sm text-success font-medium">
            <Cloud size={16} />
            Connected to Cloud — 24/7 Execution Active
          </div>
        ) : (
          <GoldButton
            fullWidth
            onClick={handleCloudLogin}
            disabled={loading || !auth.userId || !mt5Login || !mt5Password || !mt5Server}
          >
            <Cloud size={14} className="mr-2 inline" />
            {loading ? 'Connecting...' : 'Login to MT5 on Cloud'}
          </GoldButton>
        )}
      </div>
    </Card>
  );
}
