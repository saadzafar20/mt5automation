import { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import { useAppStore } from '../../store/appStore';
import { StatusPill } from './StatusPill';
import { ThemeToggle } from './ThemeToggle';

interface UpdateInfo {
  status: 'available' | 'downloading' | 'ready';
  version?: string;
  percent?: number;
}

function getInitials(userId: string): string {
  if (userId.includes('@')) {
    return userId.split('@')[0].slice(0, 2).toUpperCase();
  }
  return userId.slice(0, 2).toUpperCase();
}

export function Header() {
  const relayStatus = useAppStore((s) => s.relayStatus);
  const dots = useAppStore((s) => s.relayDots);
  const auth = useAppStore((s) => s.auth);
  const [platform, setPlatform] = useState<string>('win32');
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);

  useEffect(() => {
    const eb = window.electronBridge;
    if (eb) {
      eb.getPlatform().then(setPlatform);
      eb.onUpdateStatus((data) => setUpdateInfo(data as UpdateInfo));
    }
  }, []);

  const initials = auth.userId ? getInitials(auth.userId) : '??';
  const isOnline = relayStatus !== 'Idle' && relayStatus !== 'Offline';
  const isMac = platform === 'darwin';

  return (
    <header
      className="h-14 flex items-center justify-between pr-5 border-b border-border bg-bg-sidebar/80 backdrop-blur-xl z-30 shrink-0"
      style={{ paddingLeft: isMac ? 104 : 20, WebkitAppRegion: 'drag' } as React.CSSProperties}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 shrink-0 mr-4" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <span className="text-lg font-bold tracking-tight whitespace-nowrap">
          <span className="text-accent">Plat</span>
          <span className="text-fg">Algo</span>
        </span>
        <span className="text-[0.625rem] text-fg-faint font-medium whitespace-nowrap">Relay</span>
      </div>

      {/* Status pills */}
      <div className="flex items-center gap-2 shrink-0" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <StatusPill label="Bridge" status={dots.bridge} />
        <StatusPill label="MT5" status={dots.mt5} />
        <StatusPill label="Broker" status={dots.broker} />

        <div className={`
          flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium ml-2
          ${isOnline
            ? 'border-success/30 bg-success-bg text-success'
            : 'border-border bg-bg-hover text-fg-muted'}
        `}>
          <div className={`w-2 h-2 rounded-full ${isOnline ? 'bg-success animate-[pulse-dot_2s_ease-in-out_infinite]' : 'bg-fg-faint'}`} />
          {relayStatus}
        </div>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-3" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        {updateInfo && (
          <button
            className={`
              flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium cursor-pointer
              transition-all duration-200
              ${updateInfo.status === 'ready'
                ? 'border-accent/40 bg-accent/10 text-accent hover:bg-accent/20'
                : 'border-border bg-bg-hover text-fg-muted'}
            `}
            onClick={() => {
              if (updateInfo.status === 'ready') {
                window.electronBridge?.installUpdate();
              }
            }}
          >
            <Download size={12} />
            {updateInfo.status === 'available' && `v${updateInfo.version} available`}
            {updateInfo.status === 'downloading' && `Updating ${updateInfo.percent || 0}%`}
            {updateInfo.status === 'ready' && `Restart to update`}
          </button>
        )}
        <ThemeToggle />
        {auth.userId && (
          <div className="w-8 h-8 rounded-full bg-accent/20 border border-accent/30 flex items-center justify-center shrink-0">
            <span className="text-xs font-bold text-accent">{initials}</span>
          </div>
        )}
      </div>
    </header>
  );
}
