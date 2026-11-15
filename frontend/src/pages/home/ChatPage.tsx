import { useLayoutEffect, useRef, useState } from 'react';
import { useOutletContext } from 'react-router';
import { ArrowDown } from 'lucide-react';

import { ThinkingLoader } from '@/entities/message/ui/ThinkingLoader/ThinkingLoader';
import { SendMessage } from '@/features/send-message/ui/SendMessage';
import { useSendMessage } from '@/features/send-message/model/useSendMessage';
import { useGetChatQuery } from '@/entities/session/api/sessionApi';
import type { CreateSessionResponse } from '@/entities/session/model/session.types';
import type { StoredSession } from '@/entities/session/model/sessionStorage';

import styles from './ChatPage.module.scss';

interface ChatPageContext {
  activeSessionId: string | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  onSessionCreated: (session: CreateSessionResponse) => StoredSession;
  onFirstMessage: (sessionId: string, text: string) => void;
}

interface LocalMessage {
  id: string;
  sessionId: string | null;
  text: string;
  status: 'sending' | 'failed' | 'sent';
  assistantText?: string;
}

export function ChatPage() {
  const {
    activeSessionId,
    setActiveSessionId,
    onSessionCreated,
    onFirstMessage,
  } = useOutletContext<ChatPageContext>();

  const [localMessage, setLocalMessage] = useState<LocalMessage | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const activeSessionIdRef = useRef(activeSessionId);
  const shouldAutoScrollRef = useRef(true);
  const isScrollingToBottomRef = useRef(false);
  const isAtBottomRef = useRef(true);
  const [scrollState, setScrollState] = useState({
    sessionId: activeSessionId,
    isAtBottom: true,
  });
  const isAtBottom = scrollState.sessionId !== activeSessionId || scrollState.isAtBottom;

  if (localMessage && localMessage.sessionId !== activeSessionId) {
    setLocalMessage(null);
  }

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
  } = useGetChatQuery(
    activeSessionId ?? '',
    { skip: !activeSessionId },
  );
  const chat = currentData?.id === activeSessionId ? currentData : undefined;

  useLayoutEffect(() => {
    activeSessionIdRef.current = activeSessionId;
    shouldAutoScrollRef.current = true;
    isScrollingToBottomRef.current = false;
    isAtBottomRef.current = true;

    const messages = messagesRef.current;

    if (messages) {
      messages.scrollTop = messages.scrollHeight;
    }
  }, [activeSessionId]);

  useLayoutEffect(() => {
    const messages = messagesRef.current;

    if (!messages || !shouldAutoScrollRef.current) return;
    messages.scrollTop = messages.scrollHeight;
    isAtBottomRef.current = true;
    isAtBottomRef.current = true;
  }, [chat, localMessage?.id, localMessage?.status, localMessage?.assistantText]);

  const handleMessagesScroll = () => {
    const messages = messagesRef.current;

    if (!messages) return;

    const atBottom =
      messages.scrollHeight - messages.scrollTop - messages.clientHeight <= 32;

    if (atBottom) {
      shouldAutoScrollRef.current = true;
      isScrollingToBottomRef.current = false;
    } else if (!isScrollingToBottomRef.current) {
      shouldAutoScrollRef.current = false;
    }

    if (isAtBottomRef.current !== atBottom) {
      isAtBottomRef.current = atBottom;
      setScrollState({ sessionId: activeSessionId, isAtBottom: atBottom });
    }
  };

  const handleScrollToBottom = () => {
    const messages = messagesRef.current;

    if (!messages) return;

    shouldAutoScrollRef.current = true;
    isScrollingToBottomRef.current = true;
    messages.scrollTo({ top: messages.scrollHeight, behavior: 'smooth' });
  };

  const handleSend = async (text: string) => {
    if (
      activeSessionId &&
      chat &&
      !isHistoryLoading &&
      !isHistoryFetching &&
      !isHistoryError &&
      !chat.messages.some((message) => message.role === 'user')
    ) {
      onFirstMessage(activeSessionId, text);
    }

    const messageId = crypto.randomUUID();
    let messageSessionId = activeSessionId;

    setLocalMessage({
      id: messageId,
      sessionId: messageSessionId,
      text,
      status: 'sending',
    });

    try {
      const response = await sendMessage(text, (sessionId) => {
        messageSessionId = sessionId;

        if (activeSessionIdRef.current === null) {
          setLocalMessage((current) =>
            current?.id === messageId ? { ...current, sessionId } : current,
          );
        }
      });

      if (activeSessionIdRef.current !== response.session_id) return;

      setLocalMessage((current) =>
        current?.id === messageId
          ? {
              id: response.user_message.id,
              sessionId: response.session_id,
              text: response.user_message.text,
              status: 'sent',
              assistantText: response.assistant_message.text,
            }
          : current,
      );
    } catch (error) {
      console.error('Ошибка отправки сообщения:', error);

      if (activeSessionIdRef.current === messageSessionId) {
        setLocalMessage((current) =>
          current?.id === messageId ? { ...current, status: 'failed' } : current,
        );
      }
    }
  };

  const handleRetry = () => {
    if (!localMessage || localMessage.status !== 'failed') return;

    void handleSend(localMessage.text);
  };

  const visibleLocalMessage =
    localMessage?.sessionId === activeSessionId ? localMessage : null;

  const isLocalMessageInHistory =
    visibleLocalMessage?.status === 'sent' &&
    chat?.messages.some((message) => message.id === visibleLocalMessage.id);

  const hasChatStarted = Boolean(activeSessionId) || Boolean(visibleLocalMessage);

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
        <div className={styles.messagesContainer}>
          <div
            ref={messagesRef}
            className={styles.messages}
            onScroll={handleMessagesScroll}
          >
            {isHistoryLoading && <p>Загружаем историю...</p>}
            {isHistoryError && (
              <p role="alert">Не удалось загрузить историю диалога.</p>
            )}

            {chat?.messages.map((message) => (
              <div
                key={message.id}
                className={message.role === 'user' ? styles.userMessage : styles.assistantMessage}
              >
                {message.text}
              </div>
            ))}

            {visibleLocalMessage && !isLocalMessageInHistory && (
              <>
                <div className={styles.userMessage}>{visibleLocalMessage.text}</div>

                {visibleLocalMessage.status === 'sending' && (
                  <div className={styles.assistantMessage}>
                    <ThinkingLoader />
                  </div>
                )}

                {visibleLocalMessage.status === 'sent' && (
                  <div className={styles.assistantMessage}>{visibleLocalMessage.assistantText}</div>
                )}

                {visibleLocalMessage.status === 'failed' && (
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

          {!isAtBottom && (
            <button
              type="button"
              className={styles.scrollToBottom}
              onClick={handleScrollToBottom}
              aria-label="Прокрутить к последнему сообщению"
              title="Прокрутить вниз"
            >
              <ArrowDown size={20} aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      <div className={`${styles.inputContainer} ${hasChatStarted ? styles.bottom : ''}`}>
        <SendMessage
          onSend={handleSend}
          disabled={isLoading || Boolean(activeSessionId && isHistoryLoading)}
        />
      </div>
    </div>
  );
}
