
import type { Message } from '@/entities/message/model/types';
import type { SessionState } from '@/entities/session/model/session.types';

export interface SendMessageRequest {
  sessionId: string;
  text: string;
}

export interface SendMessageResponse {
  session_id: string;
  state: SessionState;
  user_message: Message;
  assistant_message: Message;
}
