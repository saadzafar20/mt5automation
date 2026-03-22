import { useState } from 'react';
import { Lock, Eye, EyeOff, Cloud, ExternalLink, LogIn } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { Input } from '../ui/Input';
import { OutlineButton } from '../ui/OutlineButton';
import { useAppStore } from '../../store/appStore';
import { managedEnable } from '../../lib/api';
import { bridge } from '../../lib/bridge';

export function MT5LoginCard() {
  const [mt5Login, setMt5Login] = useState('');
  const [mt5Password, setMt5Password] = useState('');
  const [mt5Server, setMt5Server] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [editing, setEditing] = useState(false);

  const auth = useAppStore((s) => s.auth);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const vpsActive = useAppStore((s) => s.vpsActive);
  const dots = useAppStore((s) => s.relayDots);

  const validate = () => {
    if (!mt5Login.trim()) return 'Account number is required';
    if (!/^\d+$/.test(mt5Login.trim())) return 'Account number must be numeric';
    if (!mt5Password) return 'Password is required';
    if (!mt5Server.trim() || mt5Server.trim().length < 3) return 'Broker server is required (e.g. ICMarkets-Live01)';
    return null;
  };

  const handleCloudLogin = async () => {
    if (!auth.userId) return;
    const err = validate();
    if (err) { setValidationError(err); return; }
    setLoading(true);
    setValidationError('');
    try {
      const result = await managedEnable({
        user_id: auth.userId,
        api_key: auth.apiKey || undefined,
        mt5_login: mt5Login.trim(),
        mt5_password: mt5Password,
        mt5_server: mt5Server.trim(),
      });
      if (result.error) {
        const lower: string = result.error.toLowerCase();
        const friendly = (lower.includes('password') || lower.includes('credentials') || lower.includes('invalid') || lower.includes('auth'))
          ? 'Incorrect credentials — please check your MT5 login and password'
          : result.error;
        toast.error(friendly);
      } else {
        setVpsActive(true);
        setEditing(false);
        toast.success('MT5 connected — 24/7 VPS Mode active');
      }
    } catch {
      toast.error('Connection failed — check your internet connection');
    } finally {
      setLoading(false);
    }
  };

  const showForm = !vpsActive || editing;

  // Prompt to sign in first
  if (!auth.userId && !vpsActive) {
    return (
      <Card>
        <div className="flex items-center gap-2 mb-5">
          <Lock size={18} className="text-danger" />
          <h2 className="text-base font-semibold text-fg">MT5 Broker Login</h2>
          <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">OPTIONAL</span>
        </div>
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <LogIn size={28} className="text-fg-faint" />
          <p className="text-sm text-fg-muted">Sign in above to configure your MT5 cloud connection</p>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center gap-2 mb-7">
        <Lock size={18} className="text-danger" />
        <h2 className="text-base font-semibold text-fg">MT5 Broker Login</h2>
        <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">OPTIONAL</span>
      </div>

      {showForm ? (
        <div className="space-y-6">
          {/* Account number */}
          <div className="space-y-2">
            <Input
              label="Account Number"
              value={mt5Login}
              onChange={(e) => { setMt5Login(e.target.value); setValidationError(''); }}
              placeholder="12345678"
              inputMode="numeric"
            />
            <p className="text-xs text-fg-muted px-0.5">
              Don&apos;t have an MT5 account?{' '}
              <button
                className="text-accent underline cursor-pointer bg-transparent border-none p-0 text-xs"
                onClick={() => bridge.openExternal('https://www.metatrader5.com/en/download')}
              >
                Download
                <ExternalLink size={10} className="inline ml-0.5 mb-0.5" />
              </button>
            </p>
          </div>

          {/* Password */}
          <div className="space-y-2">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-fg-muted">MT5 Password</label>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  value={mt5Password}
                  onChange={(e) => { setMt5Password(e.target.value); setValidationError(''); }}
                  placeholder="••••••••"
                  className="w-full bg-bg-input border border-border text-fg text-sm px-3 py-2.5 pr-10 rounded-[var(--radius)] outline-none transition-all duration-200 focus:border-accent/50 focus:shadow-[0_0_0_3px_var(--color-accent-muted)] placeholder:text-fg-faint"
                />
                <button
                  type="button"
                  className="absolute right-3 inset-y-0 flex items-center text-fg-muted hover:text-fg transition-colors cursor-pointer bg-transparent border-none"
                  onClick={() => setShowPw(!showPw)}
                >
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>
          </div>

          {/* Broker server */}
          <div className="space-y-2">
            <Input
              label="Broker Server"
              value={mt5Server}
              onChange={(e) => { setMt5Server(e.target.value); setValidationError(''); }}
              placeholder="ICMarkets-Live01"
            />
            <p className="text-xs text-fg-muted px-0.5">
              Don&apos;t have a broker?{' '}
              <button
                className="text-accent underline cursor-pointer bg-transparent border-none p-0 text-xs"
                onClick={() => bridge.openExternal('https://exness.com')}
              >
                Get one
                <ExternalLink size={10} className="inline ml-0.5 mb-0.5" />
              </button>
            </p>
          </div>

          {validationError && (
            <div className="text-xs text-danger bg-danger-bg px-3 py-2.5 rounded-lg border border-danger/20">
              {validationError}
            </div>
          )}

          <div className="flex gap-3">
            <GoldButton
              fullWidth
              onClick={handleCloudLogin}
              disabled={loading || !auth.userId}
            >
              <Cloud size={14} className="mr-2 inline" />
              {loading ? 'Connecting...' : 'Login to MT5 for 24/7 VPS Mode'}
            </GoldButton>
            {editing && (
              <OutlineButton onClick={() => { setEditing(false); setValidationError(''); }}>
                Cancel
              </OutlineButton>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className={`flex items-center gap-2 px-4 py-3 rounded-[var(--radius)] border text-sm font-medium ${
            dots.mt5 === 'online'
              ? 'bg-success-bg border-success/20 text-success'
              : 'bg-accent/10 border-accent/20 text-accent'
          }`}>
            <Cloud size={16} />
            {dots.mt5 === 'online'
              ? 'Connected to Cloud — 24/7 Execution Active'
              : 'Cloud Relay Active — MT5 Connecting...'}
          </div>

          <div className="flex gap-4 text-xs text-fg-muted px-1">
            <span className="flex items-center gap-1.5">
              <div className={`w-1.5 h-1.5 rounded-full ${dots.bridge === 'online' ? 'bg-success' : 'bg-danger'}`} />
              Bridge
            </span>
            <span className="flex items-center gap-1.5">
              <div className={`w-1.5 h-1.5 rounded-full ${dots.mt5 === 'online' ? 'bg-success' : 'bg-danger'}`} />
              MT5
            </span>
            <span className="flex items-center gap-1.5">
              <div className={`w-1.5 h-1.5 rounded-full ${dots.broker === 'online' ? 'bg-success' : 'bg-danger'}`} />
              Broker
            </span>
          </div>

          <OutlineButton size="sm" onClick={() => setEditing(true)}>
            Change Credentials
          </OutlineButton>
        </div>
      )}
    </Card>
  );
}
