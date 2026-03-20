import { motion } from 'framer-motion';
import { Sun, Moon } from 'lucide-react';
import { useAppStore } from '../../store/appStore';

export function ThemeToggle() {
  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const isDark = theme === 'dark';

  return (
    <motion.button
      onClick={toggleTheme}
      className="relative w-9 h-9 rounded-lg bg-bg-hover border border-border flex items-center justify-center cursor-pointer transition-colors duration-200 hover:border-accent-muted"
      whileHover={{ scale: 1.1 }}
      whileTap={{ scale: 0.9 }}
      title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
    >
      <motion.div
        initial={false}
        animate={{ rotate: isDark ? 0 : 180, opacity: 1 }}
        transition={{ duration: 0.3, ease: 'easeInOut' }}
      >
        {isDark ? (
          <Moon size={16} className="text-accent" />
        ) : (
          <Sun size={16} className="text-accent" />
        )}
      </motion.div>
    </motion.button>
  );
}
