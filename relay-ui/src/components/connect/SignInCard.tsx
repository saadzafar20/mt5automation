import { useState } from 'react';
import { motion } from 'framer-motion';
import { Facebook, Mail, Eye, EyeOff } from 'lucide-react';
import { Card } from '../ui/Card';
import { GoldButton } from '../ui/GoldButton';
import { Input } from '../ui/Input';
import { useAppStore } from '../../store/appStore';
import { startOAuth, consumeOAuth } from '../../lib/api';
import { bridge } from '../../lib/bridge';

export function SignInCard() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const setAuth = useAppStore((s) => s.setAuth);
  const auth = useAppStore((s) => s.auth);

  const handleOAuth = async (provider: 'google' | 'facebook') => {
    setLoading(true);
    setError('');
    try {
      const { auth_url, state } = await startOAuth(provider);
      bridge.openExternal(auth_url);
      // Poll for result
      for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const result = await consumeOAuth(state);
        if (result) {
          setAuth({ userId: result.user_id, apiKey: result.api_key, oauthProvider: provider });
          if (remember) {
            bridge.saveLastUser(JSON.stringify({ user_id: result.user_id, api_key: result.api_key, oauth_provider: provider }));
          }
          break;
        }
      }
    } catch (e) {
      setError('OAuth failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleEmailLogin = async () => {
    if (!email || !password) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch('https://app.platalgo.com/relay/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: email, password }),
      });
      const data = await res.json();
      if (data.status === 'ok' || data.token) {
        setAuth({ userId: email, apiKey: data.api_key || null });
        if (remember) {
          bridge.saveLastUser(JSON.stringify({ user_id: email }));
          bridge.setKeyringPassword('platalgo-relay', email, password);
        }
      } else {
        setError(data.error || 'Login failed');
      }
    } catch {
      setError('Connection failed');
    } finally {
      setLoading(false);
    }
  };

  if (auth.userId) {
    const initials = auth.userId.slice(0, 2).toUpperCase();
    return (
      <Card>
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-full bg-accent/20 border-2 border-accent/40 flex items-center justify-center">
            <span className="text-lg font-bold text-accent">{initials}</span>
          </div>
          <div>
            <div className="text-sm font-semibold text-fg">{auth.userId}</div>
            <div className="text-xs text-fg-muted">{auth.oauthProvider ? `Signed in via ${auth.oauthProvider}` : 'Signed in'}</div>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-base font-semibold text-fg mb-4 flex items-center gap-2">
        <Mail size={18} className="text-accent" />
        Sign In
      </h2>

      {/* OAuth buttons */}
      <div className="flex gap-3 mb-4">
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-3 rounded-[var(--radius)] bg-bg-hover border border-border text-sm font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent-muted"
          onClick={() => handleOAuth('google')}
          disabled={loading}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.94.46 3.77 1.18 4.93l3.66-2.84z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          Google
        </motion.button>
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-3 rounded-[var(--radius)] bg-bg-hover border border-border text-sm font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent-muted"
          onClick={() => handleOAuth('facebook')}
          disabled={loading}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Facebook size={16} className="text-[#1877F2]" />
          Facebook
        </motion.button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[0.625rem] text-fg-faint font-medium uppercase tracking-wider">or</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* Email/Password */}
      <div className="space-y-4">
        <Input
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
        />
        <div className="relative">
          <Input
            label="Password"
            type={showPw ? 'text' : 'password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
          <button
            className="absolute right-3 top-[calc(50%+4px)] text-fg-muted hover:text-fg transition-colors cursor-pointer bg-transparent border-none"
            onClick={() => setShowPw(!showPw)}
          >
            {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="accent-accent w-3.5 h-3.5"
          />
          <span className="text-xs text-fg-muted">Remember me</span>
        </label>

        {error && <div className="text-xs text-danger bg-danger-bg px-3 py-2 rounded-lg">{error}</div>}

        <GoldButton fullWidth onClick={handleEmailLogin} disabled={loading || !email || !password}>
          {loading ? 'Signing in...' : 'Sign In'}
        </GoldButton>
      </div>
    </Card>
  );
}
