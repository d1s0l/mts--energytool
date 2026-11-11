import { useCreateChatMutation } from '@/entities/session/api/sessionApi';
import { useSendMessageMutation } from '../api/sendMessageApi';

interface UseSendMessageProps {
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
}

export function useSendMessage({ activeSessionId, setActiveSessionId }: UseSendMessageProps) {
  const [createChat, { isLoading: isCreating }] = useCreateChatMutation();

  const [sendMessageMutation, { isLoading: isSending }] = useSendMessageMutation();

  const sendMessage = async (text: string, onSessionReady: (id: string) => void) => {
    let sessionId = activeSessionId;

    if (!sessionId) {
      const session = await createChat().unwrap();

      sessionId = session.id;
      onSessionReady(sessionId);
      setActiveSessionId(sessionId);
    }

    return sendMessageMutation({
      sessionId,
      text,
    }).unwrap();
  };

  return {
    sendMessage,
    isLoading: isCreating || isSending,
  };
}
