import { api } from './baseApi';

export interface Topic {
  id: string;
  name: string;
  keywords: string[];
  frequency: number;
  score: number;
}

export const topicsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listTopics: builder.query<{ topics: Topic[] }, { collection_id: string; limit?: number }>({
      query: ({ collection_id, limit = 50 }) =>
        `/topics?collection_id=${collection_id}&limit=${limit}`,
      providesTags: (_result, _error, { collection_id }) => [
        { type: 'Topic', id: collection_id },
      ],
    }),
    getTopicNodes: builder.query<{ topic: Topic; nodes: unknown[]; total: number }, { topic_id: string; limit?: number; offset?: number }>({
      query: ({ topic_id, limit = 100, offset = 0 }) =>
        `/topics/${topic_id}/nodes?limit=${limit}&offset=${offset}`,
      providesTags: (_result, _error, { topic_id }) => [
        { type: 'Topic', id: topic_id },
      ],
    }),
  }),
});

export const { useListTopicsQuery, useGetTopicNodesQuery } = topicsApi;
