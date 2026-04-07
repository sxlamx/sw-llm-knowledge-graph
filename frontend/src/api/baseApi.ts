import { createApi, fetchBaseQuery, BaseQueryFn, FetchArgs, FetchBaseQueryError } from '@reduxjs/toolkit/query/react';
import type { RootState } from '../store';
import { setCredentials, setAccessToken, clearCredentials } from '../store/authSlice';

const rawBaseQuery = fetchBaseQuery({
  baseUrl: '/api/v1',
  credentials: 'include',
  prepareHeaders: (headers, { getState }) => {
    const token = (getState() as RootState).auth.accessToken;
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    return headers;
  },
});

const baseQueryWithReauth: BaseQueryFn<string | FetchArgs, unknown, FetchBaseQueryError> = async (
  args,
  api,
  extraOptions,
) => {
  let result = await rawBaseQuery(args, api, extraOptions);

  if (result.error?.status === 401) {
    const refreshResult = await rawBaseQuery(
      { url: '/auth/refresh', method: 'POST' },
      api,
      extraOptions,
    );

    if (refreshResult.data) {
      const data = refreshResult.data as { access_token: string; expires_in: number };
      const state = api.getState() as RootState;
      if (state.auth.user) {
        api.dispatch(setCredentials({ accessToken: data.access_token, user: state.auth.user as import('../types/api').User }));
      } else {
        api.dispatch(setAccessToken(data.access_token));
      }
      result = await rawBaseQuery(args, api, extraOptions);
    } else {
      api.dispatch(clearCredentials());
    }
  }

  return result;
};

export const api = createApi({
  reducerPath: 'api',
  baseQuery: baseQueryWithReauth,
  tagTypes: ['Collection', 'IngestJob', 'Document', 'SearchResult', 'Ontology', 'GraphNode', 'Graph', 'Node', 'Topic'],
  endpoints: () => ({}),
});
