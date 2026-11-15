import styles from './CreateSessionButton.module.scss'
import { Plus } from 'lucide-react';
import { useCreateChatMutation } from '@/entities/session/api/sessionApi';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';

interface CreateSessionButtonProps {
  onCreated: (session: CreateSessionResponse) => void;
}

export function CreateSessionButton({
  onCreated,
}: CreateSessionButtonProps) {
  const [createChat, { isLoading }] = useCreateChatMutation();

  const handleCreate = async () => {
    try {
      const session = await createChat().unwrap();
      onCreated(session);
    } catch (error) {
      console.error('Не удалось создать диалог:', error);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCreate}
      disabled={isLoading}
      className={styles.button}
    >
      <Plus size={18} aria-hidden="true" />
      <span>{isLoading ? 'Создание...' : 'Новый диалог'}</span>
    </button>
  );
}
