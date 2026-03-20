import { motion } from 'framer-motion';
import type { ReactNode, ButtonHTMLAttributes } from 'react';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  fullWidth?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

const sizes = {
  sm: 'px-4 py-2 text-xs',
  md: 'px-6 py-2.5 text-sm',
  lg: 'px-8 py-3 text-sm',
};

export function GoldButton({ children, fullWidth, size = 'md', className = '', ...props }: Props) {
  return (
    <motion.button
      className={`btn-gold ${sizes[size]} font-semibold rounded-[var(--radius)] ${fullWidth ? 'w-full' : ''} ${className}`}
      whileHover={{ scale: 1.02, y: -1 }}
      whileTap={{ scale: 0.98 }}
      {...(props as object)}
    >
      {children}
    </motion.button>
  );
}
