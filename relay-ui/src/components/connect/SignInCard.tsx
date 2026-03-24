import { useState } from 'react';
import { motion } from 'framer-motion';
import * as Dialog from '@radix-ui/react-dialog';
import { Facebook, Mail, Eye, EyeOff, LogOut } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { Input } from '../ui/Input';
import { OutlineButton } from '../ui/OutlineButton';
import { useAppStore } from '../../store/appStore';
import { startOAuth, consumeOAuth } from '../../lib/api';
import { bridge } from '../../lib/bridge';
import { BRIDGE_URL } from '../../lib/constants';

export function SignInCard() {
  const [userIdInput, setUserIdInput] = useState('');
  const [password, setPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [showLogoutDialog, setShowLogoutDialog] = useState(false);
  const [inviteCode, setInviteCode] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);

  const setAuth = useAppStore((s) => s.setAuth);
  const clearAuth = useAppStore((s) => s.clearAuth);
  const setVpsActive = useAppStore((s) => s.setVpsActive);
  const setRelayDots = useAppStore((s) => s.setRelayDots);
  const setRelayStatus = useAppStore((s) => s.setRelayStatus);
  const auth = useAppStore((s) => s.auth);

  const handleOAuth = async (provider: 'google' | 'facebook') => {
    if (isSignUp && !inviteCode.trim()) {
      setValidationError('An invite code is required to create a new account');
      return;
    }
    setLoading(true);
    setValidationError('');
    try {
      const { auth_url, state } = await startOAuth(provider, inviteCode);
      bridge.openExternal(auth_url);
      toast.info('Browser opened — complete sign-in then return here');
      for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const result = await consumeOAuth(state);
        if (result) {
          setAuth({ userId: result.user_id, apiKey: result.api_key, oauthProvider: provider });
          if (remember) {
            bridge.saveLastUser(JSON.stringify({
              user_id: result.user_id,
              api_key: result.api_key,
              oauth_provider: provider,
            }));
          }
          toast.success(`Signed in with ${provider}`);
          return;
        }
      }
      toast.error('OAuth timed out — please try again');
    } catch {
      toast.error('OAuth failed — check your connection');
    } finally {
      setLoading(false);
    }
  };

  const handleEmailLogin = async () => {
    if (!userIdInput || !password) {
      setValidationError('User ID and password are required');
      return;
    }
    setLoading(true);
    setValidationError('');
    try {
      const endpoint = isSignUp ? `${BRIDGE_URL}/account/register` : `${BRIDGE_URL}/relay/login`;
      const body = isSignUp
        ? { user_id: email, password, invite_code: inviteCode.trim() }
        : { user_id: email, password };
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userIdInput, password }),
      });
      let data: Record<string, string> = {};
      try { data = await res.json(); } catch { /* ignore parse error */ }
      if (!res.ok) {
        toast.error(data.error || (isSignUp ? 'Registration failed' : 'Login failed — please check your credentials'));
        return;
      }
      if (data.status === 'ok' || data.token) {
        setAuth({ userId: userIdInput, apiKey: data.api_key || null });
        if (remember) {
          bridge.saveLastUser(JSON.stringify({ user_id: userIdInput, api_key: data.api_key || '' }));
          bridge.setKeyringPassword('platalgo-relay', userIdInput, password);
        }
        toast.success(isSignUp ? 'Account created successfully' : 'Signed in successfully');
      } else {
        toast.error(data.error || (isSignUp ? 'Registration failed' : 'Login failed — please check your credentials'));
      }
    } catch {
      toast.error('Connection failed — check your internet connection');
    } finally {
      setLoading(false);
    }
  };

  const confirmLogout = () => {
    clearAuth();
    setVpsActive(false);
    setRelayDots({ bridge: 'offline', mt5: 'offline', broker: 'offline' });
    setRelayStatus('Idle');
    bridge.saveLastUser(JSON.stringify({}));
    setShowLogoutDialog(false);
    toast.success('Signed out');
  };

  if (auth.userId) {
    return (
      <Card>
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-full bg-accent/20 border-2 border-accent/40 flex items-center justify-center shrink-0">
            <span className="text-lg font-bold text-accent">
              {auth.userId.includes('@')
                ? auth.userId.split('@')[0].slice(0, 2).toUpperCase()
                : auth.userId.slice(0, 2).toUpperCase()}
            </span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-fg truncate">{auth.userId}</div>
            <div className="text-xs text-fg-muted">
              {auth.oauthProvider ? `Signed in via ${auth.oauthProvider}` : 'Signed in'}
            </div>
          </div>

          <Dialog.Root open={showLogoutDialog} onOpenChange={setShowLogoutDialog}>
            <Dialog.Trigger asChild>
              <OutlineButton size="sm">
                <LogOut size={14} className="mr-1.5 inline" />
                Sign Out
              </OutlineButton>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" />
              <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 glass p-7 w-80 space-y-5">
                <Dialog.Title className="text-base font-semibold text-fg">Sign out?</Dialog.Title>
                <Dialog.Description className="text-sm text-fg-muted leading-relaxed">
                  This will disconnect your relay and clear your saved session. Any active VPS connection will continue running on the server.
                </Dialog.Description>
                <div className="flex gap-3 justify-end">
                  <Dialog.Close asChild>
                    <OutlineButton size="sm">Cancel</OutlineButton>
                  </Dialog.Close>
                  <OutlineButton size="sm" danger onClick={confirmLogout}>
                    Sign Out
                  </OutlineButton>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-[0.9rem] font-semibold text-fg flex items-center gap-2" style={{ letterSpacing: '-0.01em' }}>
          <Mail size={15} className="text-accent" />
          {isSignUp ? 'Create Account' : 'Sign In'}
        </h2>
        <button
          className="text-[0.72rem] text-accent underline cursor-pointer bg-transparent border-none p-0"
          onClick={() => { setIsSignUp(!isSignUp); setValidationError(''); setInviteCode(''); }}
        >
          {isSignUp ? 'Already have an account?' : 'Need an account?'}
        </button>
      </div>

      {/* Invite code field — shown only for sign-up */}
      {isSignUp && (
        <div className="mb-4">
          <Input
            label="Invite Code"
            type="text"
            value={inviteCode}
            onChange={(e) => { setInviteCode(e.target.value); setValidationError(''); }}
            placeholder="Enter your invite code"
          />
        </div>
      )}

      {/* OAuth buttons */}
      <div className="flex gap-2.5 mb-5">
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-[var(--radius)] bg-bg-hover/70 border border-border text-[0.8rem] font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent/20 hover:bg-primary/[0.05] disabled:opacity-50"
          onClick={() => handleOAuth('google')}
          disabled={loading}
          whileHover={{ scale: 1.015 }}
          whileTap={{ scale: 0.985 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.94.46 3.77 1.18 4.93l3.66-2.84z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          Google
        </motion.button>
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-[var(--radius)] bg-bg-hover/70 border border-border text-[0.8rem] font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent/20 hover:bg-primary/[0.05] disabled:opacity-50"
          onClick={() => handleOAuth('facebook')}
          disabled={loading}
          whileHover={{ scale: 1.015 }}
          whileTap={{ scale: 0.985 }}
        >
          <Facebook size={14} className="text-[#1877F2]" />
          Facebook
        </motion.button>
      </div>

      <div className="flex items-center gap-3 mb-5">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[0.575rem] text-fg-faint font-semibold uppercase" style={{ letterSpacing: '0.1em' }}>or</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      <div className="space-y-5">
        <Input
          label="User ID"
          type="text"
          value={userIdInput}
          onChange={(e) => { setUserIdInput(e.target.value); setValidationError(''); }}
          placeholder="your-user-id"
        />
        <div className="relative">
          <Input
            label="Password"
            type={showPw ? 'text' : 'password'}
            value={password}
            onChange={(e) => { setPassword(e.target.value); setValidationError(''); }}
            placeholder="••••••••"
          />
          <button
            className="absolute right-3 top-[calc(50%+6px)] text-fg-muted hover:text-fg transition-colors cursor-pointer bg-transparent border-none"
            onClick={() => setShowPw(!showPw)}
          >
            {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>

        <Input
          label="Invite Code (Required for new account creation)"
          type="text"
          value={inviteCode}
          onChange={(e) => setInviteCode(e.target.value)}
          placeholder="Paste invite token"
        />

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="accent-accent w-3.5 h-3.5"
          />
          <span className="text-xs text-fg-muted">Remember me</span>
        </label>

        {validationError && (
          <div className="text-xs text-danger bg-danger-bg px-3 py-2.5 rounded-lg border border-danger/20">
            {validationError}
          </div>
        )}

        <GoldButton fullWidth onClick={handleEmailLogin} disabled={loading}>
          {loading ? (isSignUp ? 'Creating account...' : 'Signing in...') : (isSignUp ? 'Create Account' : 'Sign In')}
        </GoldButton>

        <p className="text-xs text-center text-fg-muted">
          Don&apos;t have an account?{' '}
          <button
            className="text-accent underline cursor-pointer bg-transparent border-none p-0 text-xs"
            onClick={() => {
              const code = (inviteCode || '').trim();
              const url = code
                ? `https://app.platalgo.com/register?invite_code=${encodeURIComponent(code)}`
                : 'https://app.platalgo.com/register';
              bridge.openExternal(url);
            }}
          >
            Sign up
          </button>
        </p>
      </div>
    </Card>
  );
}
