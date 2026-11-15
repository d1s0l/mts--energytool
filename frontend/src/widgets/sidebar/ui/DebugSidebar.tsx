import type { SidebarSharedProps } from '../model/types';
import { SessionSidebar } from './SessionSidebar';

export function DebugSidebar(props: SidebarSharedProps) {
  return <SessionSidebar {...props} mode="debug" />;
}
