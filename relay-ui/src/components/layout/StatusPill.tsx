import type { DotStatus } from '../../store/appStore';

interface Props {
  label: string;
  status: DotStatus;
}

const dotColors: Record<DotStatus, string> = {
  online: 'bg-success',
  offline: 'bg-danger',
  unknown: 'bg-fg-faint',
};

export function StatusPill({ label, status }: Props) {
  const isOnline = status === 'online';
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-bg-hover border border-border text-xs">
      <div className={`w-2 h-2 rounded-full ${dotColors[status]} ${isOnline ? 'animate-[pulse-dot_2s_ease-in-out_infinite]' : ''}`} />
      <span className="text-fg-muted font-medium">{label}</span>
    </div>
  );
}
