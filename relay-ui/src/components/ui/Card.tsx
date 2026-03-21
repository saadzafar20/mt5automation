import { motion } from 'framer-motion';
import type { ReactNode } from 'react';

interface CardProps {
  children: ReactNode;
  gold?: boolean;
  className?: string;
  hover?: boolean;
}

export function Card({ children, gold, className = '', hover = true }: CardProps) {
  return (
    <motion.div
      className={`
        ${gold ? 'glass-gold' : 'glass'}
        p-7 h-full flex flex-col
        ${hover ? 'transition-all duration-300' : ''}
        ${className}
      `}
      whileHover={hover ? { y: -2 } : undefined}
      transition={{ duration: 0.2 }}
    >
      {children}
    </motion.div>
  );
}
