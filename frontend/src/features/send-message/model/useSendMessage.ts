import type { Dispatch, SetStateAction } from 'react';

import { useCreateChatMutation } from '@/entities/session/api/sessionApi';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { StoredSession } from '@/entities/session/model/sessionStorage';
import { useSendMessageMutation } from '../api/sendMessageApi';

interface UseSendMessageProps {
  activeSessionId: string | null;
  setActiveSessionId: Dispatch<SetStateAction<string | null>>;
  registerSession: (session: CreateSessionResponse) => StoredSession;
  onFirstMessage: (sessionId: string, text: string) => void;
}

export function useSendMessage({
  activeSessionId,
  setActiveSessionId,
  registerSession,
  onFirstMessage,
}: UseSendMessageProps) {
  const [createChat, { isLoading: isCreating }] = useCreateChatMutation();

  const [sendMessageMutation, { isLoading: isSending }] = useSendMessageMutation();

  const sendMessage = async (text: string, onSessionIdCreated?: (id: string) => void) => {
    let sessionId: string;

    if (activeSessionId) {
      sessionId = activeSessionId;
    } else {
      const session = await createChat().unwrap();
      const storedSession = registerSession(session);

      sessionId = storedSession.id;
      setActiveSessionId((currentSessionId) =>
        currentSessionId === null ? sessionId : currentSessionId,
      );
      onSessionIdCreated?.(sessionId);
      onFirstMessage(sessionId, text);
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
