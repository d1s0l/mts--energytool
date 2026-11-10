import type { Message } from '@/entities/message/model/types';

export interface SendMessageRequest {
  chatId: string;
  content: string;
}

export type SendMessageResponse = Message;