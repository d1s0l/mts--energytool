import { Trash } from 'lucide-react';
import { useState } from 'react';
import { useDeleteChatMutation } from '../../api/sessionApi';
import type { StoredSession } from '../../model/sessionStorage';
import styles from './SessionList.module.scss';

interface SessionListProps {
  sessions: StoredSession[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onDeleted?: (sessionId: string) => void;
}

export function SessionList({ sessions, activeSessionId, onSelect, onDeleted }: SessionListProps) {
  const [deleteChat, { isLoading: isDeleting }] = useDeleteChatMutation();
  const [deleteError, setDeleteError] = useState(false);

  const handleDelete = async (sessionId: string) => {
    try {
      await deleteChat(sessionId).unwrap();
      setDeleteError(false);
      onDeleted?.(sessionId);
    } catch (error) {
      console.error('Ошибка удаления чата:', error);
      setDeleteError(true);
    }
  };

  if (sessions.length === 0) {
    return <p>Здесь появятся ваши диалоги</p>;
  }

  return (
    <div className={styles.list}>
      {deleteError && <p role="alert">Не удалось удалить диалог. Попробуйте ещё раз.</p>}

      {sessions.map((session) => (
        <div
          key={session.id}
          className={`${styles.itemWrapper} ${activeSessionId === session.id ? styles.active : ''}`}
        >
          <button type="button" className={styles.item} onClick={() => onSelect(session.id)}>
            <span>{session.title || 'Новый диалог'}</span>
          </button>

          <button
            type="button"
            className={styles.deleteButton}
            onClick={() => handleDelete(session.id)}
            disabled={isDeleting}
            aria-label="Удалить диалог"
            title="Удалить диалог"
          >
            <Trash size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
