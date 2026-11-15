import { useLayoutEffect, useRef, useState } from 'react';
import { useOutletContext } from 'react-router';

import { DebugPanel } from '@/entities/message/ui/DebugPanel/DebugPanel';
import { ThinkingLoader } from '@/entities/message/ui/ThinkingLoader/ThinkingLoader';
import type { Message } from '@/entities/message/model/types';
import { useGetChatQuery, useGetSessionDebugQuery } from '@/entities/session/api/sessionApi';
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

function isNotFoundError(error: unknown): boolean {
  return Boolean(
    error && typeof error === 'object' && 'status' in error && error.status === 404,
  );
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
    error: historyError,
  } = useGetChatQuery(activeSessionId ?? '', { skip: !activeSessionId });
  const {
    currentData: debugData,
    isLoading: isDebugLoading,
    isError: isDebugError,
    error: debugError,
  } = useGetSessionDebugQuery(activeSessionId ?? '', { skip: !activeSessionId });
  const chat = currentData?.id === activeSessionId ? currentData : undefined;
  const debugItems =
    debugData?.session_id === activeSessionId ? debugData.items : [];
  const debugByMessageId = new Map(
    debugItems.map((item) => [item.message_id, item.debug] as const),
  );
  const visibleLocalMessage =
    localMessage?.sessionId === activeSessionId ? localMessage : null;
  const localAssistantMessage = visibleLocalMessage?.response?.assistant_message;
  const sessionNotFound =
    isNotFoundError(historyError) || isNotFoundError(debugError);
  const localMessageInHistory =
    visibleLocalMessage?.status === 'sent' &&
    Boolean(
      chat?.messages.some(
        (message) => message.id === visibleLocalMessage.response?.user_message.id,
      ),
    );
  const getMessageDebug = (message: Message) => {
    if (localAssistantMessage?.id === message.id && localAssistantMessage.debug !== null) {
      return localAssistantMessage.debug;
    }

    if (debugByMessageId.has(message.id)) {
      return debugByMessageId.get(message.id) ?? null;
    }

    if (localAssistantMessage?.id === message.id) {
      return localAssistantMessage.debug;
    }

    return message.debug;
  };

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
        {activeSessionId && sessionNotFound && (
          <p className={styles.error} role="alert">
            Сессия не найдена или удалена.
          </p>
        )}
        {activeSessionId && isHistoryLoading && !sessionNotFound && (
          <p className={styles.emptyState}>Загружаем историю диалога...</p>
        )}
        {activeSessionId && isHistoryError && !sessionNotFound && (
          <p className={styles.error} role="alert">
            Не удалось загрузить историю диалога.
          </p>
        )}
        {activeSessionId && isDebugLoading && !sessionNotFound && (
          <p className={styles.emptyState}>Загружаем Debug-метаданные...</p>
        )}
        {activeSessionId && isDebugError && !sessionNotFound && (
          <p className={styles.error} role="alert">
            Не удалось загрузить Debug-метаданные.
          </p>
        )}
        {chat?.messages.length === 0 && !visibleLocalMessage && !sessionNotFound && (
          <p className={styles.emptyState}>В этом диалоге пока нет сообщений.</p>
        )}

        {chat?.messages.map((message) => (
          <article
            key={message.id}
            className={message.role === 'user' ? styles.userMessage : styles.assistantMessage}
          >
            <p className={styles.messageText}>{message.text}</p>
            {message.role === 'assistant' && (
              <DebugPanel metadata={getMessageDebug(message)} />
            )}
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
