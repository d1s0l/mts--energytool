import { baseApi } from '@/app/api/baseApi';

import type {
  CreateSessionResponse,
  SessionsResponse,
  GetSessionsParams,
  SessionHistoryResponse,
  SessionDebugResponse,
} from '../model/session.types';

export const sessionApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    createChat: builder.mutation<CreateSessionResponse, void>({
      query: () => ({
        url: '/sessions',
        method: 'POST',
      }),
      invalidatesTags: ['Session'],
    }),

    getChats: builder.query<SessionsResponse, GetSessionsParams | void>({
      query: (params) => ({
        url: '/sessions',
        method: 'GET',
        params: params ?? undefined,
      }),
      providesTags: ['Session'],
    }),

    getChat: builder.query<SessionHistoryResponse, string>({
      query: (sessionId) => `/sessions/${sessionId}`,
      providesTags: (_result, _error, sessionId) => [{ type: 'Session', id: sessionId }],
    }),

    getSessionDebug: builder.query<SessionDebugResponse, string>({
      query: (sessionId) => `/sessions/${sessionId}/debug`,
      providesTags: (_result, _error, sessionId) => [{ type: 'Message', id: sessionId }],
    }),

    deleteChat: builder.mutation<void, string>({
      query: (sessionId) => ({
        url: `/sessions/${sessionId}`,
        method: 'DELETE',
      }),
      invalidatesTags: ['Session'],
    }),
  }),
});

export const {
  useCreateChatMutation,
  useGetChatsQuery,
  useGetChatQuery,
  useGetSessionDebugQuery,
  useDeleteChatMutation,
} = sessionApi;
