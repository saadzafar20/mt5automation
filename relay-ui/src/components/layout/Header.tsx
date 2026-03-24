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
  const vpsActive = useAppStore((s) => s.vpsActive);
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
  const statusLabel = vpsActive && dots.mt5 === 'online'
    ? 'VPS Active'
    : vpsActive
    ? 'VPS Connecting'
    : relayStatus;

  return (
    <header
      className="h-[52px] flex items-center justify-between pr-5 border-b border-border bg-bg-sidebar/90 backdrop-blur-xl z-30 shrink-0"
      style={{ paddingLeft: isMac ? 104 : 20, WebkitAppRegion: 'drag' } as React.CSSProperties}
    >
      {/* Logo — tight negative tracking for premium wordmark feel */}
      <div className="flex items-center gap-2.5 shrink-0 mr-4" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <span className="text-[1.05rem] font-semibold whitespace-nowrap" style={{ letterSpacing: '-0.025em' }}>
          <span className="text-accent">Plat</span>
          <span className="text-fg">Algo</span>
        </span>
        {/* RELAY as uppercase badge — signals product sub-brand */}
        <span className="text-[0.5rem] text-fg-faint/60 font-semibold uppercase whitespace-nowrap" style={{ letterSpacing: '0.13em' }}>
          Relay
        </span>
      </div>

      {/* Status pills */}
      <div className="flex items-center gap-1.5 shrink-0" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <StatusPill label="Bridge" status={dots.bridge} />
        <StatusPill label="MT5" status={dots.mt5} />
        <StatusPill label="Broker" status={dots.broker} />

        {/* Main status indicator — color shifts based on relay state */}
        <div className={`
          flex items-center gap-1.5 px-2.5 py-[3px] rounded-full border text-[0.65rem] font-medium ml-1.5 transition-all duration-500
          ${vpsActive && dots.mt5 === 'online'
            ? 'border-accent/35 bg-accent/[0.08] text-accent'
            : isOnline
            ? 'border-success/25 bg-success/[0.07] text-success'
            : 'border-border bg-bg-hover/60 text-fg-muted'}
        `}>
          <div className={`w-1.5 h-1.5 rounded-full transition-colors duration-500 ${
            vpsActive && dots.mt5 === 'online'
              ? 'bg-accent animate-[pulse-dot_2s_ease-in-out_infinite]'
              : isOnline
              ? 'bg-success animate-[pulse-dot_2s_ease-in-out_infinite]'
              : 'bg-fg-faint'
          }`} />
          {statusLabel}
        </div>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-2.5" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        {updateInfo && (
          <button
            className={`
              flex items-center gap-1.5 px-2.5 py-[3px] rounded-full border text-[0.65rem] font-semibold cursor-pointer
              transition-all duration-200
              ${updateInfo.status === 'ready'
                ? 'border-accent bg-accent text-bg hover:brightness-110 shadow-[0_0_14px_hsla(43,85%,55%,0.4)] animate-[pulse-dot_2s_ease-in-out_infinite]'
                : updateInfo.status === 'downloading'
                ? 'border-border bg-bg-hover text-fg-muted cursor-default'
                : 'border-accent/30 bg-accent/[0.06] text-accent hover:bg-accent/[0.1]'}
            `}
            onClick={() => {
              if (updateInfo.status === 'ready') {
                window.electronBridge?.installUpdate();
              }
            }}
            disabled={updateInfo.status === 'downloading'}
          >
            <Download size={11} />
            {updateInfo.status === 'available' && `v${updateInfo.version} available`}
            {updateInfo.status === 'downloading' && `${updateInfo.percent || 0}%…`}
            {updateInfo.status === 'ready' && `Restart & Update`}
          </button>
        )}
        <ThemeToggle />
        {/* User avatar — double-ring on hover signals interactivity */}
        {auth.userId && (
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-accent/30 to-accent/[0.08] border border-accent/35 flex items-center justify-center shrink-0 transition-all duration-200 hover:shadow-[0_0_0_2px_hsla(43,85%,55%,0.18)]">
            <span className="text-[0.6rem] font-bold text-accent leading-none">{initials}</span>
          </div>
        )}
      </div>
    </header>
  );
}
