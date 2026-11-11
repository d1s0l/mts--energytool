import { Trash } from 'lucide-react';
import { useGetChatsQuery, useDeleteChatMutation } from '../../api/sessionApi';
import styles from './SessionList.module.scss';

interface SessionListProps {
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onDeleted?: (sessionId: string) => void;
}

export function SessionList({ activeSessionId, onSelect, onDeleted }: SessionListProps) {
  const { data, isLoading, isError } = useGetChatsQuery({
    limit: 20,
    offset: 0,
  });

  const [deleteChat, { isLoading: isDeleting }] = useDeleteChatMutation();

  const sessions = data?.items ?? [];

  const handleDelete = async (sessionId: string) => {
    try {
      await deleteChat(sessionId).unwrap();
      onDeleted?.(sessionId);
    } catch (error) {
      console.error('Ошибка удаления чата:', error);
    }
  };

  if (isLoading) {
    return <p>Загрузка диалогов...</p>;
  }

  if (isError) {
    return <p>Не удалось загрузить диалоги</p>;
  }

  if (sessions.length === 0) {
    return <p>Здесь появятся ваши диалоги</p>;
  }

  return (
    <div className={styles.list}>
      {sessions.map((session) => (
        <div
          key={session.id}
          className={`${styles.itemWrapper} ${activeSessionId === session.id ? styles.active : ''}`}
        >
          <button type="button" className={styles.item} onClick={() => onSelect(session.id)}>
            <span>Диалог от {new Date(session.created_at).toLocaleDateString('ru-RU')}</span>
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
