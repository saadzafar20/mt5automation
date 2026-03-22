import { useState, useEffect, useRef } from 'react';
import { Lock, Eye, EyeOff, Cloud, ExternalLink, LogIn, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { Input } from '../ui/Input';
import { OutlineButton } from '../ui/OutlineButton';
import { useAppStore } from '../../store/appStore';
import { managedEnable, managedStatus } from '../../lib/api';
import { bridge } from '../../lib/bridge';

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS  = 60000; // 60 s max

const CONNECT_STEPS = [
  'Saving credentials securely…',
  'Launching dedicated MT5 session…',
  'Authenticating with broker…',
  'Enabling AutoTrading…',
];

type Phase = 'form' | 'connecting' | 'connected' | 'failed';

export function MT5LoginCard() {
  const [mt5Login, setMt5Login]       = useState('');
  const [mt5Password, setMt5Password] = useState('');
  const [mt5Server, setMt5Server]     = useState('');
  const [showPw, setShowPw]           = useState(false);
  const [validationError, setValidationError] = useState('');
  const [editing, setEditing]         = useState(false);

  // Connecting phase state
  const [phase, setPhase]       = useState<Phase>('form');
  const [stepIdx, setStepIdx]   = useState(0);
  const [elapsed, setElapsed]   = useState(0);
  const [failReason, setFailReason] = useState('');

  const pollRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAt  = useRef<number>(0);

  const auth        = useAppStore((s) => s.auth);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const vpsActive   = useAppStore((s) => s.vpsActive);
  const dots        = useAppStore((s) => s.relayDots);

  // Advance step label every ~4 s while connecting
  useEffect(() => {
    if (phase !== 'connecting') return;
    const id = setInterval(() => {
      setStepIdx((i) => Math.min(i + 1, CONNECT_STEPS.length - 1));
    }, 4000);
    return () => clearInterval(id);
  }, [phase]);

  // Elapsed-seconds ticker
  useEffect(() => {
    if (phase !== 'connecting') { setElapsed(0); return; }
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    timerRef.current = id;
    return () => clearInterval(id);
  }, [phase]);

  // Clean up on unmount
  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  const stopPolling = () => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  };

  const startPolling = (userId: string, apiKey?: string) => {
    startedAt.current = Date.now();

    const tick = async () => {
      const elapsed = Date.now() - startedAt.current;
      if (elapsed >= POLL_TIMEOUT_MS) {
        setPhase('failed');
        setFailReason('MT5 session timed out — check your credentials and try again');
        return;
      }
      try {
        const status = await managedStatus(userId, apiKey);
        if (status.connected) {
          stopPolling();
          setPhase('connected');
          setEditing(false);
          setVpsActive(true);
          toast.success('MT5 connected — 24/7 VPS Mode active');
          return;
        }
      } catch {
        // network hiccup — keep polling
      }
      pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
    };

    pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
  };

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

    setValidationError('');
    setStepIdx(0);
    setPhase('connecting');

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
        const msg = (lower.includes('password') || lower.includes('credentials') || lower.includes('invalid') || lower.includes('auth'))
          ? 'Incorrect credentials — check your MT5 login and password'
          : result.error;
        setPhase('failed');
        setFailReason(msg);
        return;
      }

      // Server accepted — now poll for actual MT5 connection
      startPolling(auth.userId, auth.apiKey || undefined);
    } catch {
      setPhase('failed');
      setFailReason('Connection failed — check your internet connection');
    }
  };

  const handleRetry = () => {
    stopPolling();
    setPhase('form');
    setFailReason('');
  };

  const showForm = (!vpsActive && phase === 'form') || editing;

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

      {/* ── CONNECTING PHASE ─────────────────────────────── */}
      {phase === 'connecting' && (
        <div className="flex flex-col items-center gap-5 py-4">
          <div className="relative">
            <div className="w-14 h-14 rounded-full border-2 border-accent/20 flex items-center justify-center">
              <Cloud size={22} className="text-accent" />
            </div>
            <Loader2 size={58} className="absolute -inset-0.5 text-accent animate-spin opacity-60" strokeWidth={1} />
          </div>
          <div className="text-center space-y-1">
            <p className="text-sm font-medium text-fg">{CONNECT_STEPS[stepIdx]}</p>
            <p className="text-xs text-fg-faint">{elapsed}s elapsed</p>
          </div>
          <div className="w-full bg-bg-input rounded-full h-1 overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-1000 rounded-full"
              style={{ width: `${Math.min((elapsed / (POLL_TIMEOUT_MS / 1000)) * 100, 95)}%` }}
            />
          </div>
          <p className="text-xs text-fg-muted text-center">
            MT5 terminals take up to 60s on first launch — hang tight
          </p>
        </div>
      )}

      {/* ── FAILED PHASE ─────────────────────────────────── */}
      {phase === 'failed' && (
        <div className="flex flex-col items-center gap-4 py-4">
          <div className="w-14 h-14 rounded-full bg-danger/10 border border-danger/20 flex items-center justify-center">
            <XCircle size={24} className="text-danger" />
          </div>
          <div className="text-center space-y-1.5">
            <p className="text-sm font-semibold text-danger">Connection failed</p>
            <p className="text-xs text-fg-muted max-w-[220px]">{failReason}</p>
          </div>
          <GoldButton onClick={handleRetry}>Try again</GoldButton>
        </div>
      )}

      {/* ── FORM ─────────────────────────────────────────── */}
      {showForm && phase !== 'connecting' && phase !== 'failed' && (
        <div className="space-y-6">
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
                Download<ExternalLink size={10} className="inline ml-0.5 mb-0.5" />
              </button>
            </p>
          </div>

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
                Get one<ExternalLink size={10} className="inline ml-0.5 mb-0.5" />
              </button>
            </p>
          </div>

          {validationError && (
            <div className="text-xs text-danger bg-danger-bg px-3 py-2.5 rounded-lg border border-danger/20">
              {validationError}
            </div>
          )}

          <div className="flex gap-3">
            <GoldButton fullWidth onClick={handleCloudLogin} disabled={!auth.userId}>
              <Cloud size={14} className="mr-2 inline" />
              Login to MT5 for 24/7 VPS Mode
            </GoldButton>
            {editing && (
              <OutlineButton onClick={() => { setEditing(false); setValidationError(''); setPhase('connected'); }}>
                Cancel
              </OutlineButton>
            )}
          </div>
        </div>
      )}

      {/* ── ACTIVE / CONNECTED ───────────────────────────── */}
      {(vpsActive || phase === 'connected') && !editing && phase !== 'connecting' && phase !== 'failed' && (
        <div className="space-y-4">
          <div className={`flex items-center gap-2 px-4 py-3 rounded-[var(--radius)] border text-sm font-medium ${
            dots.mt5 === 'online'
              ? 'bg-success-bg border-success/20 text-success'
              : 'bg-accent/10 border-accent/20 text-accent'
          }`}>
            {dots.mt5 === 'online'
              ? <CheckCircle2 size={16} />
              : <Loader2 size={16} className="animate-spin" />}
            {dots.mt5 === 'online'
              ? 'Connected to Cloud — 24/7 Execution Active'
              : 'Cloud Relay Active — MT5 Connecting…'}
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

          <OutlineButton size="sm" onClick={() => { setEditing(true); setPhase('form'); }}>
            Change Credentials
          </OutlineButton>
        </div>
      )}
    </Card>
  );
}
