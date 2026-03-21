import { api } from './baseApi';

export interface NodeScore {
  node_id: string;
  label: string;
  score: number;
}

export interface AnalyticsResponse {
  collection_id: string;
  metric: string;
  scores: NodeScore[];
  communities: Record<string, string>;
}

export interface AnalyticsSummary {
  collection_id: string;
  node_count: number;
  edge_count: number;
  num_communities: number;
  top_pagerank: Array<{ id: string; label: string; score: number }>;
  top_betweenness: Array<{ id: string; label: string; score: number }>;
}

export const analyticsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    getPageRank: builder.query<AnalyticsResponse, { collection_id: string; top_k?: number }>({
      query: ({ collection_id, top_k = 50 }) =>
        `/analytics/pagerank?collection_id=${collection_id}&top_k=${top_k}`,
    }),
    getBetweenness: builder.query<AnalyticsResponse, { collection_id: string; top_k?: number }>({
      query: ({ collection_id, top_k = 50 }) =>
        `/analytics/betweenness?collection_id=${collection_id}&top_k=${top_k}`,
    }),
    getCommunities: builder.query<AnalyticsResponse, { collection_id: string }>({
      query: ({ collection_id }) =>
        `/analytics/communities?collection_id=${collection_id}`,
    }),
    getAnalyticsSummary: builder.query<AnalyticsSummary, { collection_id: string }>({
      query: ({ collection_id }) =>
        `/analytics/summary?collection_id=${collection_id}`,
    }),
  }),
});

export const {
  useGetPageRankQuery,
  useGetBetweennessQuery,
  useGetCommunitiesQuery,
  useGetAnalyticsSummaryQuery,
} = analyticsApi;
