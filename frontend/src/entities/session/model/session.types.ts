import type { DebugMetadata, Message } from '@/entities/message/model/types';

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
  first_message?: string | null;
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

export interface SessionDebugItem {
  message_id: string;
  created_at: string;
  debug: DebugMetadata | null;
}

export interface SessionDebugResponse {
  session_id: string;
  items: SessionDebugItem[];
}

// Ответ POST /api/sessions
export interface CreateSessionResponse {
  id: string;
  access_token: string;
  state: SessionState;
  created_at: string;
  deleted_at: string | null;
}
