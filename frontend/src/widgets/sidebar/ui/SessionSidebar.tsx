import styles from './Sidebar.module.scss';

import { SessionList } from '@/entities/session/ui/SessionList/SessionList';
import { CreateSessionButton } from '@/features/create-session/ui/CreateSessionButton/CreateSessionButton';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { SidebarSharedProps } from '../model/types';

type SessionSidebarProps = SidebarSharedProps &
  (
    | { mode: 'chat'; onCreateSession: (session: CreateSessionResponse) => void }
    | { mode: 'debug' }
  );

export function SessionSidebar(props: SessionSidebarProps) {
  const { sessions, activeSessionId, width, isResizing, onResizePointerDown, onSelectSession, onDeleteSession } =
    props;
  const isDebugSidebar = props.mode === 'debug';

  return (
    <aside
      className={`${styles.sidebar} ${isResizing ? styles.resizing : ''} ${isDebugSidebar ? styles.debugSidebar : ''}`}
      style={{ '--sidebar-width': `${width}px` } as React.CSSProperties}
      aria-label="История диалогов"
    >
      {props.mode === 'chat' && (
        <div className={styles.top}>
          <CreateSessionButton onCreated={props.onCreateSession} />
        </div>
      )}

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
