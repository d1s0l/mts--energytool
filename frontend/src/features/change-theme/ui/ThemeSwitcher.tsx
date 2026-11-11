import { Moon, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';

import styles from './ThemeSwitcher.module.scss';

export function ThemeSwitcher() {
  const [isDark, setIsDark] = useState(
    () => localStorage.getItem('theme') === 'dark',
  );

  useEffect(() => {
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
  }, [isDark]);

  return (
    <div className={styles.block}>
      <Sun size={20} aria-hidden="true" />

      <button
        className={`${styles.switch} ${isDark ? styles.active : ''}`}
        onClick={() => setIsDark((prev) => !prev)}
        type="button"
        aria-label="Toggle theme"
        aria-pressed={isDark}
      >
        <span className={styles.thumb} />
      </button>

      <Moon size={20} aria-hidden="true" />
    </div>
  );
}