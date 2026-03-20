import { useAppStore } from '../../store/appStore';
import { StatusPill } from './StatusPill';
import { ThemeToggle } from './ThemeToggle';

export function Header() {
  const relayStatus = useAppStore((s) => s.relayStatus);
  const dots = useAppStore((s) => s.relayDots);
  const auth = useAppStore((s) => s.auth);

  const initials = auth.userId ? auth.userId.slice(0, 2).toUpperCase() : '??';
  const isOnline = relayStatus !== 'Idle' && relayStatus !== 'Offline';

  return (
    <header className="h-14 flex items-center justify-between px-5 border-b border-border bg-bg-sidebar/80 backdrop-blur-xl z-30 shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold tracking-tight">
          <span className="text-accent">Plat</span>
          <span className="text-fg">Algo</span>
        </span>
        <span className="text-[0.625rem] text-fg-faint font-medium">Relay</span>
      </div>

      {/* Status pills */}
      <div className="flex items-center gap-2">
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
      <div className="flex items-center gap-3">
        <ThemeToggle />
        {auth.userId && (
          <div className="w-8 h-8 rounded-full bg-accent/20 border border-accent/30 flex items-center justify-center">
            <span className="text-xs font-bold text-accent">{initials}</span>
          </div>
        )}
      </div>
    </header>
  );
}
