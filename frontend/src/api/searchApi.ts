import { api } from './baseApi';
import type { SearchRequest, SearchResponse } from '../types/api';

export const searchApi = api.injectEndpoints({
  endpoints: (builder) => ({
    search: builder.mutation<SearchResponse, SearchRequest>({
      query: (body) => ({ url: '/search', method: 'POST', body }),
    }),
  }),
});

export const { useSearchMutation } = searchApi;
