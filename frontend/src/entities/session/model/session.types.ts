import type { Message } from '@/entities/message/model/types';

// Состояние сессии
export type SessionState =
  | 'chatting'
  | 'awaiting_contacts'
  | 'lead_created';

// Основная информация о сессии
export interface Session {
  id: string;
  state: SessionState;
  created_at: string;
}

// Ответ GET /api/sessions
export interface SessionsResponse {
  items: Session[];
  total: number;
  limit: number;
  offset: number;
}

// Параметры GET /api/sessions
export interface GetSessionsParams {
  limit?: number;
  offset?: number;
}

// Ответ GET /api/sessions/{session_id}
export interface SessionHistoryResponse extends Session {
  messages: Message[];
}

// Ответ POST /api/sessions
export type CreateSessionResponse = Session;
