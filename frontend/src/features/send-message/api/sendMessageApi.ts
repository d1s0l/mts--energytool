import { baseApi } from '@/app/api/baseApi';
import type {
  SendMessageRequest,
  SendMessageResponse,
} from '@/entities/send-message/model/types';

export const sendMessageApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    sendMessage: builder.mutation<
      SendMessageResponse,
      SendMessageRequest
    >({
      query: ({ chatId, content }) => ({
        url: `/chats/${chatId}/messages`, 
        method: 'POST',
        body: { content }, 
      }),

      invalidatesTags: (_result, _error, { chatId }) => [
        { type: 'Message', id: chatId },
      ],
    }),
  }),
});

export const { useSendMessageMutation } = sendMessageApi;