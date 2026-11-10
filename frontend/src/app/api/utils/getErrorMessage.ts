import type { ApiError } from '../types/apiError';

export function getErrorMessage(error: ApiError): string {
  if ('status' in error) {
    if (typeof error.data === 'string') {
      return error.data;
    }

    if (
      error.data &&
      typeof error.data === 'object' &&
      'detail' in error.data &&
      typeof error.data.detail === 'string'
    ) {
      return error.data.detail;
    }

    if (typeof error.status === 'number') {
      return `Ошибка сервера: ${error.status}`;
    }

    return 'Не удалось выполнить запрос';
  }

  return error.message ?? 'Произошла неизвестная ошибка';
}