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

// Serialise the proactive refresh so concurrent requests don't each trigger one.
let refreshInProgress: Promise<void> | null = null;

const baseQueryWithReauth: BaseQueryFn<string | FetchArgs, unknown, FetchBaseQueryError> = async (
  args,
  api,
  extraOptions
) => {
  // If no token in Redux state, proactively refresh BEFORE making the request
  // so we never hit the API with a missing token (eliminates page-load 401s).
  const state = api.getState() as RootState;
  if (!state.auth.accessToken) {
    if (!refreshInProgress) {
      refreshInProgress = (async () => {
        const refreshResult = await rawBaseQuery(
          { url: '/auth/refresh', method: 'POST' },
          api,
          extraOptions
        );
        if (refreshResult.data) {
          const { access_token } = refreshResult.data as { access_token: string };
          api.dispatch(setAccessToken(access_token));
        } else {
          api.dispatch(logout());
        }
      })().finally(() => { refreshInProgress = null; });
    }
    await refreshInProgress;
    // If still no token after refresh the user is not logged in — abort.
    const afterRefresh = api.getState() as RootState;
    if (!afterRefresh.auth.accessToken) {
      return { error: { status: 401, data: 'Unauthorized' } as FetchBaseQueryError };
    }
  }

  let result = await rawBaseQuery(args, api, extraOptions);

  // Handle mid-session token expiry
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
