import { MessageSquare, Plus } from 'lucide-react';
import styles from './Sidebar.module.scss';
import type { ChatSummary } from '../model/types';

interface SidebarProps {
  chats: ChatSummary[];
  activeChatId: string | null;
  width: number;
  isResizing: boolean;
  onResizePointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onCreateChat: () => void;
  onSelectChat: (chatId: string) => void;
}

export function Sidebar({
  chats,
  activeChatId,
  width,
  isResizing,
  onResizePointerDown,
  onCreateChat,
  onSelectChat,
}: SidebarProps) {
  return (
    <aside
      className={`${styles.sidebar} ${isResizing ? styles.resizing : ''}`}
      style={{ '--sidebar-width': `${width}px` } as React.CSSProperties}
      aria-label="История диалогов"
    >
      <div className={styles.top}>
        <button className={styles.createButton} onClick={onCreateChat} type="button">
          <Plus size={18} aria-hidden="true" />
          <span>Новый диалог</span>
        </button>
      </div>

      <section className={styles.history} aria-labelledby="chat-history-title">
        <h2 className={styles.sectionTitle} id="chat-history-title">
          История диалогов
        </h2>
        <div className={styles.chatList}>
          {chats.length === 0 ? (
            <p className={styles.emptyState}>Здесь появятся ваши диалоги</p>
          ) : (
            chats.map((chat) => (
              <button
                aria-current={activeChatId === chat.id ? 'page' : undefined}
                className={`${styles.chatItem} ${activeChatId === chat.id ? styles.active : ''}`}
                key={chat.id}
                onClick={() => onSelectChat(chat.id)}
                title={chat.title}
                type="button"
              >
                <MessageSquare size={17} aria-hidden="true" />
                <span>{chat.title}</span>
              </button>
            ))
          )}
        </div>
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
