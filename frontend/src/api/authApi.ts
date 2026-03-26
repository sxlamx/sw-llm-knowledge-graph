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
    exchangeCode: builder.mutation<AuthResponse, { code: string; redirect_uri: string }>({
      query: (body) => ({
        url: '/auth/google/exchange',
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

export const { useGoogleLoginMutation, useExchangeCodeMutation, useLogoutMutation } = authApi;

/** Compat alias */
export const useLogoutUserMutation = useLogoutMutation;
