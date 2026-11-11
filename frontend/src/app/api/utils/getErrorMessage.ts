export function getErrorMessage(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return 'Не удалось выполнить запрос';
  }

  if ('data' in error) {
    const data = error.data;
    if (typeof data === 'string') return data;
    if (data && typeof data === 'object') {
      if ('error' in data && data.error && typeof data.error === 'object' &&
          'message' in data.error && typeof data.error.message === 'string') {
        return data.error.message;
      }
      if ('detail' in data && typeof data.detail === 'string') return data.detail;
    }
  }

  if ('message' in error && typeof error.message === 'string') return error.message;
  return 'Не удалось выполнить запрос';
}
