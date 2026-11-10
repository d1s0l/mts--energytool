import { Bug } from 'lucide-react';
import { Link, useLocation } from 'react-router';
import styles from './DebugButton.module.scss'

export function DebugButton() {
  const location = useLocation();

  const isDebug = location.pathname === '/debug';

  return (
    <Link to={isDebug ? '/' : '/debug'} className={styles.debug_mode}>
      <Bug size={18} />
      <span>{isDebug ? 'Вернуться в чат' : 'Режим отладки'}</span>
    </Link>
  );
}
