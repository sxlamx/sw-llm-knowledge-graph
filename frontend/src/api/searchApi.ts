import { api } from './baseApi';
import type { SearchRequest, SearchResponse, SearchResult } from '../types/api';

export type { SearchResult };
/** Compat alias */
export type SearchResultItem = SearchResult;

export const searchApi = api.injectEndpoints({
  endpoints: (builder) => ({
    search: builder.mutation<SearchResponse, SearchRequest>({
      query: (body) => ({ url: '/search', method: 'POST', body }),
    }),
    getSearchSuggestions: builder.query<string[], { q: string; collection_id?: string }>({
      query: ({ q, collection_id }) =>
        `/search/suggestions?q=${encodeURIComponent(q)}${collection_id ? `&collection_id=${collection_id}` : ''}`,
    }),
  }),
});

export const { useSearchMutation, useGetSearchSuggestionsQuery } = searchApi;
