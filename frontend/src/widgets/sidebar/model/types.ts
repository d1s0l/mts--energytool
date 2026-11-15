import type { PointerEvent } from 'react';
import type { StoredSession } from '@/entities/session/model/sessionStorage';

export interface ChatSummary {
  id: string;
  title: string;
}

export interface SidebarSharedProps {
  sessions: StoredSession[];
  activeSessionId: string | null;
  width: number;
  isResizing: boolean;
  onResizePointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onSelectSession: (sessionId: string | null) => void;
  onDeleteSession: (sessionId: string) => void;
}
