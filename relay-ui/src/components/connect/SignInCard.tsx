import { useState } from 'react';
import { motion } from 'framer-motion';
import { Chrome, Facebook, Mail, Eye, EyeOff } from 'lucide-react';
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
      <h2 className="text-sm font-semibold text-fg mb-4 flex items-center gap-2">
        <Mail size={16} className="text-accent" />
        Sign In
      </h2>

      {/* OAuth buttons */}
      <div className="flex gap-3 mb-4">
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-[var(--radius)] bg-bg-hover border border-border text-sm font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent-muted"
          onClick={() => handleOAuth('google')}
          disabled={loading}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Chrome size={16} className="text-fg-soft" />
          Google
        </motion.button>
        <motion.button
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-[var(--radius)] bg-bg-hover border border-border text-sm font-medium text-fg cursor-pointer transition-all duration-200 hover:border-accent-muted"
          onClick={() => handleOAuth('facebook')}
          disabled={loading}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Facebook size={16} className="text-blue" />
          Facebook
        </motion.button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[0.625rem] text-fg-faint font-medium uppercase tracking-wider">or</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* Email/Password */}
      <div className="space-y-3">
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
