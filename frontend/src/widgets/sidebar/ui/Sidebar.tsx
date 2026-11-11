import styles from './Sidebar.module.scss';

import { SessionList } from '@/entities/session/ui/SessionList/SessionList';
import { CreateSessionButton } from '@/features/create-session/ui/CreateSessionButton/CreateSessionButton';

interface SidebarProps {
  activeSessionId: string | null;
  width: number;
  isResizing: boolean;
  onResizePointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onSelectSession: (sessionId: string | null) => void;
}

export function Sidebar({
  activeSessionId,
  width,
  isResizing,
  onResizePointerDown,
  onSelectSession,
}: SidebarProps) {
  return (
    <aside
      className={`${styles.sidebar} ${isResizing ? styles.resizing : ''}`}
      style={{ '--sidebar-width': `${width}px` } as React.CSSProperties}
      aria-label="История диалогов"
    >
      <div className={styles.top}>
        <CreateSessionButton onCreated={onSelectSession} />
      </div>

      <section className={styles.history} aria-labelledby="chat-history-title">
        <h2 className={styles.sectionTitle} id="chat-history-title">
          История диалогов
        </h2>

        <SessionList
          activeSessionId={activeSessionId}
          onSelect={onSelectSession}
          onDeleted={(sessionId) => {
            if (activeSessionId === sessionId) {
              onSelectSession(null);
            }
          }}
        />
      </section>

      <div className={styles.footer}>
        <span className={styles.footerAccent} aria-hidden="true" />
        <p>Ваш помощник от МТС</p>
      </div>

      <div
        className={styles.resizeHandle}
        role="separator"
        aria-orientation="vertical"
        aria-label="Изменить ширину боковой панели"
        onPointerDown={onResizePointerDown}
      />
    </aside>
  );
}
