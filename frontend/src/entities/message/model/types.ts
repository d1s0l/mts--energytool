export type MessageRole = 'user' | 'assistant';

// Источник информации из базы знаний
export interface DebugSource {
  document_id: string;
  title: string;
  fragment_id: string;
  fragment: string;
  score: number | null;
}

// Метаданные отладки
export interface DebugMetadata {
  total_ms: number;
  search_ms: number | null;
  generation_ms: number | null;
  sources: DebugSource[];
  raw_response?: string | null;
  raw_response_attempts?: string[] | null;
  parsed_response?: unknown;
  validation_errors?: string[] | null;
  final_response?: string | null;
}

// Сообщение
export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  created_at: string;
  debug: DebugMetadata | null;
}
