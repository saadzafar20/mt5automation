import { useRef } from 'react';
import { motion, useInView } from 'framer-motion';
import type { ReactNode } from 'react';

type Variant = 'fade-up' | 'slide-left' | 'slide-right' | 'scale' | 'scale-rotate';

interface Props {
  children: ReactNode;
  variant?: Variant;
  delay?: number;
  duration?: number;
  className?: string;
}

const variants: Record<Variant, { hidden: object; visible: object }> = {
  'fade-up': {
    hidden: { opacity: 0, y: 24 },
    visible: { opacity: 1, y: 0 },
  },
  'slide-left': {
    hidden: { opacity: 0, x: -40 },
    visible: { opacity: 1, x: 0 },
  },
  'slide-right': {
    hidden: { opacity: 0, x: 40 },
    visible: { opacity: 1, x: 0 },
  },
  'scale': {
    hidden: { opacity: 0, scale: 0.85 },
    visible: { opacity: 1, scale: 1 },
  },
  'scale-rotate': {
    hidden: { opacity: 0, scale: 0.8, rotate: -5 },
    visible: { opacity: 1, scale: 1, rotate: 0 },
  },
};

export function ScrollReveal({
  children,
  variant = 'fade-up',
  delay = 0,
  duration = 0.5,
  className = '',
}: Props) {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-50px' });
  const v = variants[variant];

  return (
    <motion.div
      ref={ref}
      initial={v.hidden as import('framer-motion').TargetAndTransition}
      animate={(isInView ? v.visible : v.hidden) as import('framer-motion').TargetAndTransition}
      transition={{ duration, delay, ease: 'easeOut' }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
