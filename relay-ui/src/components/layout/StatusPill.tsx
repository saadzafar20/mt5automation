import * as Tooltip from '@radix-ui/react-tooltip';
import type { DotStatus } from '../../store/appStore';

interface Props {
  label: string;
  status: DotStatus;
}

const dotColors: Record<DotStatus, string> = {
  online: 'bg-success',
  offline: 'bg-danger',
  unknown: 'bg-[hsl(40,80%,55%)]',
};

const tooltipText: Record<DotStatus, string> = {
  online: 'Connected',
  offline: 'Disconnected',
  unknown: 'Checking…',
};

const tooltipColors: Record<DotStatus, string> = {
  online: 'text-success',
  offline: 'text-danger',
  unknown: 'text-fg-muted',
};

const pillBorder: Record<DotStatus, string> = {
  online: 'border-success/22',
  offline: 'border-danger/22',
  unknown: 'border-border',
};

export function StatusPill({ label, status }: Props) {
  const isOnline = status === 'online';

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* Color-matched border per status — more information per pixel */}
        <div className={`flex items-center gap-1.5 px-2 py-[3px] rounded-full bg-bg-hover/60 border ${pillBorder[status]} text-[0.65rem] cursor-default transition-colors duration-300`}>
          <div
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColors[status]} ${isOnline ? 'animate-[pulse-dot_2s_ease-in-out_infinite]' : ''}`}
          />
          <span className="text-fg-muted font-medium">{label}</span>
        </div>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          sideOffset={6}
          className="z-50 px-2.5 py-1.5 rounded-lg text-[0.7rem] font-medium bg-bg-card border border-border text-fg shadow-lg select-none"
        >
          <span className={tooltipColors[status]}>{tooltipText[status]}</span>
          <span className="text-fg-muted ml-1">— {label}</span>
          <Tooltip.Arrow className="fill-border" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
