import { api } from './baseApi';
import type { AuthResponse } from '../types/api';

export const authApi = api.injectEndpoints({
  endpoints: (builder) => ({
    googleLogin: builder.mutation<AuthResponse, { token: string }>({
      query: (body) => ({
        url: '/auth/google',
        method: 'POST',
        body,
      }),
    }),
    logout: builder.mutation<void, void>({
      query: () => ({ url: '/auth/logout', method: 'POST' }),
      invalidatesTags: ['Collection', 'IngestJob', 'Document'],
    }),
  }),
});

export const { useGoogleLoginMutation, useLogoutMutation } = authApi;
