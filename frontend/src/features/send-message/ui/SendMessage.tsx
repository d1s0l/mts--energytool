
import {
  useState,
  useRef,
  useLayoutEffect,
  type FormEvent,
  type KeyboardEvent,
} from 'react';

import { ArrowUp } from 'lucide-react';
import styles from './MessageInput.module.scss';

interface MessageInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

const MIN_HEIGHT = 36;
const MAX_HEIGHT = 160;

export function SendMessage({
  onSend,
  disabled = false,
  placeholder = 'Напишите сообщение...',
}: MessageInputProps) {
  const [message, setMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;

    if (!textarea) return;

    textarea.style.height = 'auto';

    const height = Math.min(
      Math.max(textarea.scrollHeight, MIN_HEIGHT),
      MAX_HEIGHT,
    );

    textarea.style.height = `${height}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden';
  }, [message]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmedMessage = message.trim();

    if (!trimmedMessage || disabled) return;

    onSend(trimmedMessage);
    setMessage('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <textarea
        ref={textareaRef}
        className={styles.input}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
      />

      <button
        className={styles.sendButton}
        type="submit"
        disabled={!message.trim() || disabled}
        aria-label="Отправить сообщение"
      >
        <ArrowUp size={20} />
      </button>
    </form>
  );
}
