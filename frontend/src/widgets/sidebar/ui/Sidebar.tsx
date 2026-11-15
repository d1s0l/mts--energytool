import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { SidebarSharedProps } from '../model/types';
import { SessionSidebar } from './SessionSidebar';

interface SidebarProps extends SidebarSharedProps {
  onCreateSession: (session: CreateSessionResponse) => void;
}

export function Sidebar(props: SidebarProps) {
  return <SessionSidebar {...props} mode="chat" />;
}
