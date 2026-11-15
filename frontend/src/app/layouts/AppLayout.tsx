
import { useState } from 'react';
import type { PointerEvent } from 'react';
import { Outlet, useLocation } from 'react-router';

import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import {
  addStoredSession,
  getStoredSessions,
  removeStoredSession,
  updateStoredSession,
  type StoredSession,
} from '@/entities/session/model/sessionStorage';
import { Header } from '@/widgets/header/Header';
import { DebugSidebar, Sidebar } from '@/widgets/sidebar';

import styles from './AppLayout.module.scss';

const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 450;
const INITIAL_SIDEBAR_WIDTH = 280;

export function AppLayout() {
  const isDebugRoute = useLocation().pathname === '/debug';
  const [sidebarWidth, setSidebarWidth] = useState(INITIAL_SIDEBAR_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<StoredSession[]>(getStoredSessions);

  const handleSessionCreated = (session: CreateSessionResponse): StoredSession => {
    const storedSession = addStoredSession({
      id: session.id,
      access_token: session.access_token,
      created_at: session.created_at,
      title: 'Новый диалог',
    });

    setSessions(getStoredSessions());
    setActiveSessionId(storedSession.id);
    return storedSession;
  };

  const handleFirstMessage = (sessionId: string, text: string) => {
    const session = getStoredSessions().find((item) => item.id === sessionId);
    if (session?.title !== 'Новый диалог') return;

    setSessions(updateStoredSession(sessionId, { title: text }));
  };

  const handleSessionDeleted = (sessionId: string) => {
    setSessions(removeStoredSession(sessionId));
    setActiveSessionId((currentSessionId) =>
      currentSessionId === sessionId ? null : currentSessionId,
    );
  };

  const handleResizePointerDown = (
    event: PointerEvent<HTMLDivElement>,
  ) => {
    if (
      event.button !== 0 ||
      window.matchMedia('(max-width: 760px)').matches
    ) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsResizing(true);

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      const availableWidth = document.documentElement.clientWidth;
      const maximumWidth = Math.min(MAX_SIDEBAR_WIDTH, availableWidth);

      setSidebarWidth(
        Math.max(
          MIN_SIDEBAR_WIDTH,
          Math.min(maximumWidth, moveEvent.clientX),
        ),
      );
    };

    const finishResize = () => {
      setIsResizing(false);

      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', finishResize);
      window.removeEventListener('pointercancel', finishResize);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', finishResize);
    window.addEventListener('pointercancel', finishResize);
  };

  return (
    <div className={styles.layout}>
      <Header />

      <div className={`${styles.content} ${isDebugRoute ? styles.debugContent : ''}`}>
        {isDebugRoute ? (
          <DebugSidebar
            sessions={sessions}
            activeSessionId={activeSessionId}
            width={sidebarWidth}
            isResizing={isResizing}
            onResizePointerDown={handleResizePointerDown}
            onSelectSession={setActiveSessionId}
            onDeleteSession={handleSessionDeleted}
          />
        ) : (
          <Sidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          width={sidebarWidth}
          isResizing={isResizing}
          onResizePointerDown={handleResizePointerDown}
          onSelectSession={setActiveSessionId}
          onCreateSession={handleSessionCreated}
          onDeleteSession={handleSessionDeleted}
          />
        )}

        <main className={styles.main}>
          <Outlet
            context={{
              activeSessionId,
              setActiveSessionId,
              onSessionCreated: handleSessionCreated,
              onFirstMessage: handleFirstMessage,
            }}
          />
        </main>
      </div>
    </div>
  );
}
