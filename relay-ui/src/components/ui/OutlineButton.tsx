import { motion } from 'framer-motion';
import type { ReactNode, ButtonHTMLAttributes } from 'react';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  danger?: boolean;
  fullWidth?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

const sizes = {
  sm: 'px-4 py-2 text-xs',
  md: 'px-6 py-2.5 text-sm',
  lg: 'px-8 py-3 text-sm',
};

export function OutlineButton({ children, danger, fullWidth, size = 'md', className = '', ...props }: Props) {
  return (
    <motion.button
      className={`
        ${danger
          ? 'bg-danger-bg text-danger border border-danger/20 hover:border-danger/40'
          : 'bg-bg-hover text-fg border border-border hover:border-accent-muted'}
        ${sizes[size]}
        font-medium rounded-[var(--radius)] cursor-pointer transition-all duration-200
        ${fullWidth ? 'w-full' : ''}
        ${className}
      `}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      {...(props as object)}
    >
      {children}
    </motion.button>
  );
}
