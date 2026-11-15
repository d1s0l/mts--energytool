import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react';
import type { FetchArgs } from '@reduxjs/toolkit/query';

import { getStoredSessionToken } from '@/entities/session/model/sessionStorage';

const rawBaseQuery = fetchBaseQuery({
  baseUrl: import.meta.env.VITE_API_URL || '/api',
});

function getSessionIdFromUrl(url: string): string | undefined {
  const match = url.split('?')[0].match(/(?:^|\/)sessions\/([^/]+)(?:\/|$)/);
  if (!match) return undefined;

  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

export const baseApi = createApi({
  reducerPath: 'baseApi',

  baseQuery: async (args, api, extraOptions) => {
    const request: FetchArgs = typeof args === 'string' ? { url: args } : args;
    const sessionId = getSessionIdFromUrl(request.url);

    if (sessionId) {
      const token = getStoredSessionToken(sessionId);
      if (!token) {
        return {
          error: {
            status: 'CUSTOM_ERROR',
            error: 'Токен доступа для этого диалога не найден',
          },
        };
      }

      const headers = new Headers();
      if (request.headers instanceof Headers) {
        request.headers.forEach((value, name) => headers.set(name, value));
      } else if (Array.isArray(request.headers)) {
        request.headers.forEach(([name, value]) => headers.set(name, value));
      } else if (request.headers) {
        Object.entries(request.headers).forEach(([name, value]) => {
          if (value !== undefined) headers.set(name, value);
        });
      }

      headers.set('X-Session-Token', token);
      request.headers = headers;
    }

    return rawBaseQuery(request, api, extraOptions);
  },

  tagTypes: ['Session', 'Message', 'Lead'],

  endpoints: () => ({}),
});
