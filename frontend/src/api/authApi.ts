import { api } from './baseApi';

export interface AuthResponse {
  access_token: string;
  user: {
    id: string;
    email: string;
    name: string;
    picture?: string;
  };
}

export const authApi = api.injectEndpoints({
  endpoints: (builder) => ({
    googleLogin: builder.mutation<AuthResponse, { id_token: string }>({
      query: (body) => ({ url: '/auth/google', method: 'POST', body }),
    }),
    refresh: builder.mutation<{ access_token: string }, void>({
      query: () => ({ url: '/auth/refresh', method: 'POST' }),
    }),
    logoutUser: builder.mutation<void, void>({
      query: () => ({ url: '/auth/logout', method: 'POST' }),
    }),
  }),
});

export const { useGoogleLoginMutation, useRefreshMutation, useLogoutUserMutation } = authApi;
