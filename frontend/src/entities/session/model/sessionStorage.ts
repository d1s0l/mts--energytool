export interface StoredSession {
  id: string;
  access_token: string;
  created_at: string;
  title: string;
}

const STORAGE_KEY = 'mts-energytool.sessions';

function isStoredSession(value: unknown): value is StoredSession {
  if (!value || typeof value !== 'object') return false;

  const session = value as Record<string, unknown>;

  return (
    typeof session.id === 'string' &&
    session.id.length > 0 &&
    typeof session.access_token === 'string' &&
    session.access_token.length > 0 &&
    typeof session.created_at === 'string' &&
    typeof session.title === 'string'
  );
}

function uniqueSessions(sessions: StoredSession[]): StoredSession[] {
  const seenIds = new Set<string>();

  return sessions.filter((session) => {
    if (seenIds.has(session.id)) return false;

    seenIds.add(session.id);
    return true;
  });
}

export function getStoredSessions(): StoredSession[] {
  try {
    const storedValue = localStorage.getItem(STORAGE_KEY);
    if (!storedValue) return [];

    const parsed: unknown = JSON.parse(storedValue);
    if (!Array.isArray(parsed)) {
      throw new Error('Список диалогов должен быть массивом');
    }

    return uniqueSessions(parsed.filter(isStoredSession));
  } catch (error) {
    console.error('Не удалось прочитать список диалогов из localStorage:', error);
    return [];
  }
}

export function saveStoredSessions(sessions: StoredSession[]): StoredSession[] {
  const unique = uniqueSessions(sessions);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(unique));
  return unique;
}

export function addStoredSession(session: StoredSession): StoredSession {
  if (!isStoredSession(session)) {
    throw new Error('Ответ создания диалога не содержит обязательные данные');
  }

  const sessions = getStoredSessions();
  const existingSession = sessions.find((item) => item.id === session.id);
  if (existingSession) return existingSession;

  saveStoredSessions([...sessions, session]);
  return session;
}

export function updateStoredSession(
  sessionId: string,
  updates: Partial<Pick<StoredSession, 'created_at' | 'title'>>,
): StoredSession[] {
  const sessions = getStoredSessions().map((session) =>
    session.id === sessionId ? { ...session, ...updates } : session,
  );

  return saveStoredSessions(sessions);
}

export function removeStoredSession(sessionId: string): StoredSession[] {
  return saveStoredSessions(getStoredSessions().filter((session) => session.id !== sessionId));
}

export function getStoredSessionToken(sessionId: string): string | undefined {
  return getStoredSessions().find((session) => session.id === sessionId)?.access_token || undefined;
}
