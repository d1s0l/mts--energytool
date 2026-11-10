import { useOutletContext } from 'react-router';

interface ChatPageContext {
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
}

export function ChatPage() {
  const {
    activeSessionId,
    setActiveSessionId,
  } = useOutletContext<ChatPageContext>();

  return (
    <div>
      {activeSessionId
        ? `Активный диалог: ${activeSessionId}`
        : 'Выберите диалог или создайте новый'}
    </div>
  );
}