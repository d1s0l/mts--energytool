import type { ApiError } from '@/app/api/types/apiError';
import { getErrorMessage } from '@/app/api/utils/getErrorMessage';
import { useSendMessageMutation } from '@/features/send-message/api/sendMessageApi';
import { SendMessage } from '@/features/send-message/ui/SendMessage';

export function Chat() {
  const [sendMessage, { isLoading, error }] = useSendMessageMutation();

  const handleSendMessage = async (message: string) => {
    try {
      const response = await sendMessage({
        sessionId: 'current-chat-id',
        text: message,
      }).unwrap();

      console.log(response);
    } catch (error) {
      console.error(getErrorMessage(error as ApiError));
    }
  };

  return (
    <section>
      {error && <p>Не удалось отправить сообщение</p>}

      <SendMessage onSend={handleSendMessage} disabled={isLoading} />
    </section>
  );
}
