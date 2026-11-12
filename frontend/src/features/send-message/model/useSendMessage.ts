import type { Dispatch, SetStateAction } from 'react';

import { useCreateChatMutation } from '@/entities/session/api/sessionApi';
import { useSendMessageMutation } from '../api/sendMessageApi';

interface UseSendMessageProps {
  activeSessionId: string | null;
  setActiveSessionId: Dispatch<SetStateAction<string | null>>;
}

export function useSendMessage({ activeSessionId, setActiveSessionId }: UseSendMessageProps) {
  const [createChat, { isLoading: isCreating }] = useCreateChatMutation();

  const [sendMessageMutation, { isLoading: isSending }] = useSendMessageMutation();

  const sendMessage = async (text: string, onSessionCreated?: (id: string) => void) => {
    let sessionId = activeSessionId;

    if (!sessionId) {
      const session = await createChat().unwrap();

      sessionId = session.id;
      setActiveSessionId((currentSessionId) =>
        currentSessionId === null ? sessionId : currentSessionId,
      );
      onSessionCreated?.(sessionId);
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
