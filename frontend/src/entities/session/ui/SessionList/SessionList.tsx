import { useGetChatsQuery } from '../../api/sessionApi';
import styles from './SessionList.module.scss';

interface SessionListProps {
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
}

export function SessionList({
  activeSessionId,
  onSelect,
}: SessionListProps) {
  const { data, isLoading, isError } = useGetChatsQuery({
    limit: 20,
    offset: 0,
  });

  const sessions = data?.items ?? [];

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
        <button
          key={session.id}
          type="button"
          className={
            activeSessionId === session.id
              ? styles.active
              : styles.item
          }
          onClick={() => onSelect(session.id)}
        >
          Диалог от{' '}
          {new Date(session.created_at).toLocaleDateString('ru-RU')}
        </button>
      ))}
    </div>
  );
}
