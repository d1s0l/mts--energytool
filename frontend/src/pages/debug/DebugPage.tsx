import { useLayoutEffect, useRef, useState } from 'react';
import { useOutletContext } from 'react-router';

import { DebugPanel } from '@/entities/message/ui/DebugPanel/DebugPanel';
import { ThinkingLoader } from '@/entities/message/ui/ThinkingLoader/ThinkingLoader';
import { useGetChatQuery } from '@/entities/session/api/sessionApi';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { StoredSession } from '@/entities/session/model/sessionStorage';
import type { SendMessageResponse } from '@/entities/send-message/model/types';
import { useSendMessage } from '@/features/send-message/model/useSendMessage';
import { SendMessage } from '@/features/send-message/ui/SendMessage';

import styles from './DebugPage.module.scss';

interface DebugPageContext {
  activeSessionId: string | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  onSessionCreated: (session: CreateSessionResponse) => StoredSession;
  onFirstMessage: (sessionId: string, text: string) => void;
}

interface LocalDebugMessage {
  id: string;
  sessionId: string;
  text: string;
  status: 'sending' | 'failed' | 'sent';
  response?: SendMessageResponse;
}

export function DebugPage() {
  const {
    activeSessionId,
    setActiveSessionId,
    onSessionCreated,
    onFirstMessage,
  } = useOutletContext<DebugPageContext>();
  const [localMessage, setLocalMessage] = useState<LocalDebugMessage | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  const { sendMessage, isLoading } = useSendMessage({
    activeSessionId,
    setActiveSessionId,
    registerSession: onSessionCreated,
    onFirstMessage,
  });

  const {
    currentData,
    isLoading: isHistoryLoading,
    isFetching: isHistoryFetching,
    isError: isHistoryError,
  } = useGetChatQuery(activeSessionId ?? '', { skip: !activeSessionId });
  const chat = currentData?.id === activeSessionId ? currentData : undefined;
  const visibleLocalMessage =
    localMessage?.sessionId === activeSessionId ? localMessage : null;
  const localMessageInHistory =
    visibleLocalMessage?.status === 'sent' &&
    Boolean(
      chat?.messages.some(
        (message) => message.id === visibleLocalMessage.response?.user_message.id,
      ),
    );

  useLayoutEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [activeSessionId, chat, visibleLocalMessage?.id, visibleLocalMessage?.status]);

  const handleSend = async (text: string) => {
    if (!activeSessionId) return;

    if (
      chat &&
      !isHistoryLoading &&
      !isHistoryFetching &&
      !isHistoryError &&
      !chat.messages.some((message) => message.role === 'user')
    ) {
      onFirstMessage(activeSessionId, text);
    }

    const messageId = crypto.randomUUID();
    const sessionId = activeSessionId;
    setLocalMessage({ id: messageId, sessionId, text, status: 'sending' });

    try {
      const response = await sendMessage(text);
      setLocalMessage((current) =>
        current?.id === messageId ? { ...current, status: 'sent', response } : current,
      );
    } catch (error) {
      console.error('Ошибка отправки сообщения в Debug:', error);
      setLocalMessage((current) =>
        current?.id === messageId ? { ...current, status: 'failed' } : current,
      );
    }
  };

  const handleRetry = () => {
    if (visibleLocalMessage?.status === 'failed') {
      void handleSend(visibleLocalMessage.text);
    }
  };

  return (
    <section className={styles.debugPage} aria-label="Отладка диалога">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>MTS Energytool</p>
          <h1>Панель отладки</h1>
        </div>
        {activeSessionId && <code className={styles.sessionId}>{activeSessionId}</code>}
      </header>

      <div className={styles.messages} ref={messagesRef}>
        {!activeSessionId && (
          <p className={styles.emptyState}>Выберите диалог в боковой панели.</p>
        )}
        {activeSessionId && isHistoryLoading && (
          <p className={styles.emptyState}>Загружаем историю диалога...</p>
        )}
        {activeSessionId && isHistoryError && (
          <p className={styles.error} role="alert">
            Не удалось загрузить историю диалога.
          </p>
        )}

        {chat?.messages.map((message) => (
          <article
            key={message.id}
            className={message.role === 'user' ? styles.userMessage : styles.assistantMessage}
          >
            <p className={styles.messageText}>{message.text}</p>
            {message.role === 'assistant' && <DebugPanel metadata={message.debug} />}
          </article>
        ))}

        {visibleLocalMessage && !localMessageInHistory && (
          <>
            <article className={styles.userMessage}>
              <p className={styles.messageText}>{visibleLocalMessage.text}</p>
            </article>

            {visibleLocalMessage.status === 'sending' && (
              <article className={styles.assistantMessage}>
                <ThinkingLoader />
              </article>
            )}

            {visibleLocalMessage.status === 'sent' && visibleLocalMessage.response && (
              <article className={styles.assistantMessage}>
                <p className={styles.messageText}>
                  {visibleLocalMessage.response.assistant_message.text}
                </p>
                <DebugPanel metadata={visibleLocalMessage.response.assistant_message.debug} />
              </article>
            )}

            {visibleLocalMessage.status === 'failed' && (
              <article className={styles.assistantMessage}>
                <p className={styles.error} role="alert">
                  Не удалось отправить сообщение.
                </p>
                <button type="button" className={styles.retryButton} onClick={handleRetry}>
                  Повторить
                </button>
              </article>
            )}
          </>
        )}
      </div>

      <div className={styles.composer}>
        <SendMessage
          onSend={handleSend}
          disabled={
            !activeSessionId ||
            isLoading ||
            Boolean(activeSessionId && (isHistoryLoading || isHistoryFetching))
          }
        />
      </div>
    </section>
  );
}
