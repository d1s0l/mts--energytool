
import { useState } from 'react';
import type { PointerEvent } from 'react';
import { Outlet } from 'react-router';

import { Header } from '@/widgets/header/Header';
import { Sidebar } from '@/widgets/sidebar';

import styles from './AppLayout.module.scss';

const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 450;
const INITIAL_SIDEBAR_WIDTH = 280;

export function AppLayout() {
  const [sidebarWidth, setSidebarWidth] = useState(INITIAL_SIDEBAR_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

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

      <div className={styles.content}>
        <Sidebar
          activeSessionId={activeSessionId}
          width={sidebarWidth}
          isResizing={isResizing}
          onResizePointerDown={handleResizePointerDown}
          onSelectSession={setActiveSessionId}
        />

        <main className={styles.main}>
          <Outlet
            context={{
              activeSessionId,
              setActiveSessionId,
            }}
          />
        </main>
      </div>
    </div>
  );
}
