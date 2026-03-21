import { api } from './baseApi';
import { SearchMode } from '../store/slices/searchSlice';

export interface SearchResultItem {
  chunk_id: string;
  doc_id: string;
  doc_title?: string;
  text: string;
  page?: number;
  vector_score: number;
  keyword_score: number;
  graph_proximity_score: number;
  final_score: number;
  topics: string[];
  highlights: string[];
  has_image?: boolean;
  image_b64?: string;
}

export interface SearchResponse {
  results: SearchResultItem[];
  total: number;
  offset: number;
  limit: number;
  latency_ms: number;
  search_mode: string;
}

export interface SearchRequest {
  query: string;
  collection_ids?: string[];
  topics?: string[];
  limit?: number;
  offset?: number;
  mode?: SearchMode;
  weights?: { vector: number; keyword: number; graph: number };
}

export const searchApi = api.injectEndpoints({
  endpoints: (builder) => ({
    search: builder.mutation<SearchResponse, SearchRequest>({
      query: (body) => ({ url: '/search', method: 'POST', body }),
    }),
    getSearchSuggestions: builder.query<
      { suggestions: string[] },
      { q: string; collection_id?: string }
    >({
      query: ({ q, collection_id }) => {
        const params = new URLSearchParams({ q });
        if (collection_id) params.set('collection_id', collection_id);
        return `/search/suggestions?${params.toString()}`;
      },
    }),
  }),
});

export const { useSearchMutation, useGetSearchSuggestionsQuery } = searchApi;
