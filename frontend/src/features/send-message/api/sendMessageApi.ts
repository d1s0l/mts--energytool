import { baseApi } from '@/app/api/baseApi';
import type { SendMessageRequest, SendMessageResponse } from '@/entities/send-message/model/types';

export const sendMessageApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    sendMessage: builder.mutation<SendMessageResponse, SendMessageRequest>({
      query: ({ sessionId, text }) => ({
        url: `/sessions/${sessionId}/messages`,
        method: 'POST',
        body: { text },
      }),

      invalidatesTags: (_result, _error, { sessionId }) => [
        'Session',
        { type: 'Session', id: sessionId },
      ],
    }),
  }),
});

export const { useSendMessageMutation } = sendMessageApi;
