import { useState } from 'react';
import { getErrorMessage } from '@/app/api/utils/getErrorMessage';
import { useOutletContext } from 'react-router';

import { ThinkingLoader } from '@/entities/message/ui/ThinkingLoader/ThinkingLoader';
import { SendMessage } from '@/features/send-message/ui/SendMessage';
import { useSendMessage } from '@/features/send-message/model/useSendMessage';
import { useGetChatQuery } from '@/entities/session/api/sessionApi';

import styles from './ChatPage.module.scss';

interface ChatPageContext {
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
}

interface LocalMessage {
  id: string;
  sessionId: string | null;
  error?: string;
  text: string;
  status: 'sending' | 'failed' | 'sent';
  assistantText?: string;
}

export function ChatPage() {
  const { activeSessionId, setActiveSessionId } = useOutletContext<ChatPageContext>();

  const [localMessage, setLocalMessage] = useState<LocalMessage | null>(null);

  const { sendMessage, isLoading } = useSendMessage({
    activeSessionId,
    setActiveSessionId,
  });

  const { currentData: chat, isFetching: isHistoryLoading, error: historyError } = useGetChatQuery(activeSessionId ?? '', {
    skip: !activeSessionId,
  });

  const handleSend = async (text: string) => {
    const messageId = crypto.randomUUID();

    setLocalMessage({
      id: messageId,
      sessionId: activeSessionId,
      text,
      status: 'sending',
    });

    try {
      const response = await sendMessage(text, (sessionId) => {
        setLocalMessage((current) => current?.id === messageId
          ? { ...current, sessionId }
          : current);
      });

      setLocalMessage((current) => current?.id === messageId ? {
        sessionId: response.session_id,
        id: response.user_message.id,
        text: response.user_message.text,
        status: 'sent',
        assistantText: response.assistant_message.text,
      } : current);
      return true;
    } catch (error) {
      setLocalMessage((current) =>
        current?.id === messageId ? { ...current, status: 'failed', error: getErrorMessage(error) } : current,
      );
      return false;
    }
  };

  const handleRetry = () => {
    if (!localMessage || localMessage.status !== 'failed') return;

    void handleSend(localMessage.text);
  };

  const visibleMessage = localMessage?.sessionId === activeSessionId ? localMessage : null;

  const isLocalMessageInHistory =
    visibleMessage?.status === 'sent' &&
    chat?.messages.some((message) => message.id === visibleMessage.id);

  const hasChatStarted = Boolean(activeSessionId || visibleMessage);

  return (
    <div className={styles.chatPage}>
      {!hasChatStarted && (
        <div className={styles.welcome}>
          <h1 className={styles.title}>Чем могу помочь?</h1>

          <p className={styles.description}>
            Задайте вопрос по энергетике, оборудованию или документации. Я помогу найти нужную
            информацию.
          </p>
        </div>
      )}

      {hasChatStarted && (
        <div className={styles.messages}>
          {isHistoryLoading && <p>Загружаем историю...</p>}
          {historyError && <p role="alert">{getErrorMessage(historyError)}</p>}

          {chat?.messages.map((message) => (
            <div
              key={message.id}
              className={message.role === 'user' ? styles.userMessage : styles.assistantMessage}
            >
              {message.text}
            </div>
          ))}

          {visibleMessage && !isLocalMessageInHistory && (
            <>
              <div className={styles.userMessage}>{visibleMessage.text}</div>

              {visibleMessage.status === 'sending' && (
                <div className={styles.assistantMessage}>
                  <ThinkingLoader />
                </div>
              )}

              {visibleMessage.status === 'sent' && (
                <div className={styles.assistantMessage}>{visibleMessage.assistantText}</div>
              )}

              {visibleMessage.status === 'failed' && (
                <div className={styles.assistantMessage}>
                  <p role="alert">{visibleMessage.error}</p>

                  <button type="button" onClick={handleRetry} className={styles.retryButton}>
                    Повторить
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      <div className={`${styles.inputContainer} ${hasChatStarted ? styles.bottom : ''}`}>
        <SendMessage onSend={handleSend} disabled={isLoading} />
      </div>
    </div>
  );
}
