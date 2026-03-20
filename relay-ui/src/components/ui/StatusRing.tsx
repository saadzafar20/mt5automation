import { motion } from 'framer-motion';
import type { DotStatus } from '../../store/appStore';

interface Props {
  label: string;
  letter: string;
  description: string;
  status: DotStatus;
}

const statusColors: Record<DotStatus, string> = {
  online: 'var(--color-success)',
  offline: 'var(--color-danger)',
  unknown: 'var(--color-fg-faint)',
};

export function StatusRing({ label, letter, description, status }: Props) {
  const color = statusColors[status];
  const isOnline = status === 'online';

  return (
    <div className="flex flex-col items-center gap-3">
      <motion.div
        className="relative w-20 h-20 rounded-full flex items-center justify-center"
        style={{
          border: `3px solid ${color}`,
          boxShadow: isOnline ? `0 0 20px ${color}40` : 'none',
        }}
        animate={isOnline ? {
          boxShadow: [`0 0 20px ${color}40`, `0 0 30px ${color}60`, `0 0 20px ${color}40`],
        } : {}}
        transition={{ duration: 2, repeat: Infinity }}
      >
        <span className="text-2xl font-bold" style={{ color }}>{letter}</span>
      </motion.div>
      <div className="text-center">
        <div className="text-sm font-semibold text-fg">{label}</div>
        <div className="flex items-center justify-center gap-1.5 mt-1">
          <div
            className="w-2 h-2 rounded-full"
            style={{
              background: color,
              animation: isOnline ? 'pulse-dot 2s ease-in-out infinite' : 'none',
            }}
          />
          <span className="text-xs text-fg-muted capitalize">{status}</span>
        </div>
        <div className="text-[0.625rem] text-fg-faint mt-0.5">{description}</div>
      </div>
    </div>
  );
}
