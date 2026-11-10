import { useState } from 'react';
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
  text: string;
  status: 'sending' | 'failed' | 'sent';
  assistantText?: string;
}

export function ChatPage() {
  const { activeSessionId, setActiveSessionId } = useOutletContext<ChatPageContext>();

  const [isChatStarted, setIsChatStarted] = useState(false);
  const [localMessage, setLocalMessage] = useState<LocalMessage | null>(null);

  const { sendMessage, isLoading } = useSendMessage({
    activeSessionId,
    setActiveSessionId,
  });

  const { data: chat, isLoading: isHistoryLoading } = useGetChatQuery(activeSessionId ?? '', {
    skip: !activeSessionId,
  });

  const handleSend = async (text: string) => {
    setIsChatStarted(true);

    const messageId = crypto.randomUUID();

    setLocalMessage({
      id: messageId,
      text,
      status: 'sending',
    });

    try {
      const response = await sendMessage(text);

      setLocalMessage({
        id: response.user_message.id,
        text: response.user_message.text,
        status: 'sent',
        assistantText: response.assistant_message.text,
      });
    } catch (error) {
      console.error('Ошибка отправки сообщения:', error);

      setLocalMessage((current) =>
        current?.id === messageId ? { ...current, status: 'failed' } : current,
      );
    }
  };

  const handleRetry = () => {
    if (!localMessage || localMessage.status !== 'failed') return;

    void handleSend(localMessage.text);
  };

  const isLocalMessageInHistory =
    localMessage?.status === 'sent' &&
    chat?.messages.some((message) => message.id === localMessage.id);

  const hasChatStarted = isChatStarted || Boolean(activeSessionId);

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

          {chat?.messages.map((message) => (
            <div
              key={message.id}
              className={message.role === 'user' ? styles.userMessage : styles.assistantMessage}
            >
              {message.text}
            </div>
          ))}

          {localMessage && !isLocalMessageInHistory && (
            <>
              <div className={styles.userMessage}>{localMessage.text}</div>

              {localMessage.status === 'sending' && (
                <div className={styles.assistantMessage}>
                  <ThinkingLoader />
                </div>
              )}

              {localMessage.status === 'sent' && (
                <div className={styles.assistantMessage}>{localMessage.assistantText}</div>
              )}

              {localMessage.status === 'failed' && (
                <div className={styles.assistantMessage}>
                  <p>Не удалось отправить сообщение. Попробуйте ещё раз.</p>

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
