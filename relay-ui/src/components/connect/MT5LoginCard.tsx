import { useState } from 'react';
import { Lock, Eye, EyeOff } from 'lucide-react';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';

interface Props {
  onCredentials?: (creds: { mt5Login: string; mt5Password: string; mt5Server: string }) => void;
}

export function MT5LoginCard({ onCredentials }: Props) {
  const [mt5Login, setMt5Login] = useState('');
  const [mt5Password, setMt5Password] = useState('');
  const [mt5Server, setMt5Server] = useState('');
  const [showPw, setShowPw] = useState(false);

  const handleChange = () => {
    onCredentials?.({ mt5Login, mt5Password, mt5Server });
  };

  return (
    <Card>
      <div className="flex items-center gap-2 mb-4">
        <Lock size={16} className="text-danger" />
        <h2 className="text-sm font-semibold text-fg">MT5 Broker Login</h2>
        <span className="text-[0.5rem] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded">OPTIONAL</span>
      </div>

      <div className="space-y-3">
        <Input
          label="Account Number"
          value={mt5Login}
          onChange={(e) => { setMt5Login(e.target.value); handleChange(); }}
          placeholder="12345678"
        />
        <div className="relative">
          <Input
            label="MT5 Password"
            type={showPw ? 'text' : 'password'}
            value={mt5Password}
            onChange={(e) => { setMt5Password(e.target.value); handleChange(); }}
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
          onChange={(e) => { setMt5Server(e.target.value); handleChange(); }}
          placeholder="ICMarkets-Live01"
        />
      </div>
    </Card>
  );
}
