import { baseApi } from '@/app/api/baseApi';
import type { CreateSessionResponse, SessionsResponse, GetSessionsParams } from '../model/session.types';

export const sessionApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    createChat: builder.mutation<CreateSessionResponse, void>({
      query: () => ({
        url: '/sessions',
        method: 'POST',
      }),
    }),
    getChats: builder.query<SessionsResponse, GetSessionsParams>({
      query: (params) => ({
        url: '/sessions',
        method: 'GET',
        params,
      }),
      providesTags: ['Session'],
    }),
  }),
});

export const {
  useCreateChatMutation,
  useGetChatsQuery,
} = sessionApi;
