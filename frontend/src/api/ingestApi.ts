import { api } from './baseApi';

export interface IngestJob {
  id: string;
  collection_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  total_docs: number;
  processed_docs: number;
  current_file?: string;
  error_msg?: string;
  started_at?: string;
  completed_at?: string;
  created_at?: string;
  stream_url?: string;
}

export interface IngestFolderRequest {
  collection_id: string;
  folder_path: string;
  options?: {
    chunk_size?: number;
    chunk_overlap?: number;
    extract_entities?: boolean;
    max_cost_usd?: number;
  };
}

export const ingestApi = api.injectEndpoints({
  endpoints: (builder) => ({
    startIngestJob: builder.mutation<IngestJob, IngestFolderRequest>({
      query: (body) => ({ url: '/ingest/folder', method: 'POST', body }),
      invalidatesTags: ['IngestJob'],
    }),
    listJobs: builder.query<{ jobs: IngestJob[]; total: number }, string | void>({
      query: (collection_id) =>
        collection_id ? `/ingest/jobs?collection_id=${collection_id}` : '/ingest/jobs',
      providesTags: ['IngestJob'],
    }),
    getJob: builder.query<IngestJob, string>({
      query: (id) => `/ingest/jobs/${id}`,
      providesTags: (_result, _error, id) => [{ type: 'IngestJob', id }],
    }),
    cancelJob: builder.mutation<void, string>({
      query: (id) => ({ url: `/ingest/jobs/${id}`, method: 'DELETE' }),
      invalidatesTags: ['IngestJob'],
    }),
  }),
});

export const {
  useStartIngestJobMutation,
  useListJobsQuery,
  useGetJobQuery,
  useCancelJobMutation,
} = ingestApi;
