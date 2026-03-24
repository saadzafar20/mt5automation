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
        className="relative w-[68px] h-[68px] rounded-full flex items-center justify-center"
        style={{
          border: `2px solid ${color}`,
          boxShadow: isOnline ? `0 0 18px ${color}38` : 'none',
        }}
        animate={isOnline ? {
          boxShadow: [`0 0 16px ${color}38`, `0 0 28px ${color}55`, `0 0 16px ${color}38`],
        } : {}}
        transition={{ duration: 2.2, repeat: Infinity }}
      >
        {/* IBM Plex Mono for the letter — matches the data typography system */}
        <span
          className="text-xl font-semibold"
          style={{ color, fontFamily: 'var(--font-mono)', letterSpacing: '-0.02em' }}
        >
          {letter}
        </span>
      </motion.div>
      <div className="text-center">
        <div className="text-[0.8rem] font-semibold text-fg" style={{ letterSpacing: '-0.01em' }}>{label}</div>
        <div className="flex items-center justify-center gap-1.5 mt-1">
          <div
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{
              background: color,
              animation: isOnline ? 'pulse-dot 2s ease-in-out infinite' : 'none',
            }}
          />
          <span className="text-[0.65rem] text-fg-muted capitalize">{status}</span>
        </div>
        <div className="text-[0.575rem] text-fg-faint mt-0.5" style={{ letterSpacing: '0.02em' }}>{description}</div>
      </div>
    </div>
  );
}
