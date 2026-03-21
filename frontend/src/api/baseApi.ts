import { createApi, fetchBaseQuery, retry, BaseQueryFn, FetchArgs, FetchBaseQueryError } from '@reduxjs/toolkit/query/react';
import { RootState } from '../store';
import { setAccessToken, logout } from '../store/slices/authSlice';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

const rawBaseQuery = fetchBaseQuery({
  baseUrl: API_BASE,
  prepareHeaders: (headers, { getState }) => {
    const token = (getState() as RootState).auth.accessToken;
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return headers;
  },
  credentials: 'include',
});

const baseQueryWithReauth: BaseQueryFn<string | FetchArgs, unknown, FetchBaseQueryError> = async (
  args,
  api,
  extraOptions
) => {
  let result = await rawBaseQuery(args, api, extraOptions);

  if (result.error?.status === 401) {
    const refreshResult = await rawBaseQuery(
      { url: '/auth/refresh', method: 'POST' },
      api,
      extraOptions
    );
    if (refreshResult.data) {
      const { access_token } = refreshResult.data as { access_token: string };
      api.dispatch(setAccessToken(access_token));
      result = await rawBaseQuery(args, api, extraOptions);
    } else {
      api.dispatch(logout());
    }
  }

  return result;
};

// Retry up to 3 times on 5xx / network errors; never retry on 4xx (client errors).
const retryingBaseQuery = retry(baseQueryWithReauth, {
  maxRetries: 3,
  backoff: async (attempt) => {
    await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * 2 ** attempt, 10_000)));
  },
  retryCondition: (error) => {
    const status = (error as FetchBaseQueryError)?.status;
    if (typeof status === 'number' && status >= 400 && status < 500) return false;
    return true;
  },
});

export const api = createApi({
  reducerPath: 'api',
  baseQuery: retryingBaseQuery,
  tagTypes: [
    'Collection',
    'Document',
    'IngestJob',
    'SearchResult',
    'GraphNode',
    'Ontology',
    'Topic',
  ],
  endpoints: () => ({}),
});
