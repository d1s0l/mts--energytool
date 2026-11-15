import styles from './Sidebar.module.scss';

import { SessionList } from '@/entities/session/ui/SessionList/SessionList';
import { CreateSessionButton } from '@/features/create-session/ui/CreateSessionButton/CreateSessionButton';
import { DebugButton } from '@/features/debug-mode-button/ui/DebugButton';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { StoredSession } from '@/entities/session/model/sessionStorage';

interface SidebarProps {
  sessions: StoredSession[];
  activeSessionId: string | null;
  width: number;
  isResizing: boolean;
  onResizePointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onSelectSession: (sessionId: string | null) => void;
  onCreateSession: (session: CreateSessionResponse) => void;
  onDeleteSession: (sessionId: string) => void;
  isDebugRoute: boolean;
}

export function Sidebar({
  sessions,
  activeSessionId,
  width,
  isResizing,
  onResizePointerDown,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  isDebugRoute,
}: SidebarProps) {
  return (
    <aside
      className={`${styles.sidebar} ${isResizing ? styles.resizing : ''} ${isDebugRoute ? styles.debugSidebar : ''}`}
      style={{ '--sidebar-width': `${width}px` } as React.CSSProperties}
      aria-label="История диалогов"
    >
      <div className={styles.top}>
        <CreateSessionButton onCreated={onCreateSession} />
      </div>

      <section className={styles.history} aria-labelledby="chat-history-title">
        <h2 className={styles.sectionTitle} id="chat-history-title">
          История диалогов
        </h2>

        <SessionList
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelect={onSelectSession}
          onDeleted={onDeleteSession}
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
