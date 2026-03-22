import * as Tooltip from '@radix-ui/react-tooltip';
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

const tooltipText: Record<DotStatus, string> = {
  online: 'Connected',
  offline: 'Disconnected',
  unknown: 'Status unknown',
};

export function StatusPill({ label, status }: Props) {
  const isOnline = status === 'online';

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-bg-hover border border-border text-xs cursor-default">
          <div
            className={`w-2 h-2 rounded-full ${dotColors[status]} ${isOnline ? 'animate-[pulse-dot_2s_ease-in-out_infinite]' : ''}`}
          />
          <span className="text-fg-muted font-medium">{label}</span>
        </div>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          sideOffset={6}
          className="z-50 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-bg-card border border-border text-fg shadow-lg select-none"
        >
          <span className={isOnline ? 'text-success' : 'text-danger'}>{tooltipText[status]}</span>
          <span className="text-fg-muted ml-1">— {label}</span>
          <Tooltip.Arrow className="fill-border" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
