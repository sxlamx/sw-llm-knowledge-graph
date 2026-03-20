import { api } from './baseApi';
import type { IngestJob, IngestStartRequest } from '../types/api';

export const ingestApi = api.injectEndpoints({
  endpoints: (builder) => ({
    startIngest: builder.mutation<IngestJob, IngestStartRequest>({
      query: (body) => ({ url: '/ingest/folder', method: 'POST', body }),
      invalidatesTags: ['IngestJob'],
    }),
    listJobs: builder.query<{ jobs: IngestJob[] }, string | void>({
      query: (collection_id) =>
        collection_id ? `/ingest/jobs?collection_id=${collection_id}` : '/ingest/jobs',
      providesTags: ['IngestJob'],
    }),
    getJob: builder.query<IngestJob, string>({
      query: (id) => `/ingest/jobs/${id}`,
      providesTags: (_r, _e, id) => [{ type: 'IngestJob', id }],
    }),
    cancelJob: builder.mutation<void, string>({
      query: (id) => ({ url: `/ingest/jobs/${id}/cancel`, method: 'POST' }),
      invalidatesTags: ['IngestJob'],
    }),
  }),
});

export const {
  useStartIngestMutation,
  useListJobsQuery,
  useGetJobQuery,
  useCancelJobMutation,
} = ingestApi;
