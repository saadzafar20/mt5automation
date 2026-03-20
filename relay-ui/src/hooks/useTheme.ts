import { useEffect } from 'react';
import { useAppStore } from '../store/appStore';

export function useThemeInit() {
  const theme = useAppStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
}
