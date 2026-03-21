import { api } from './baseApi';

export interface FineTuneExample {
  messages: Array<{ role: string; content: string }>;
}

export interface ExportDatasetResponse {
  collection_id: string;
  example_count: number;
  examples: FineTuneExample[];
  total: number;
}

export interface StartFineTuneRequest {
  collection_id: string;
  base_model?: string;
  suffix?: string;
  n_epochs?: number;
  max_examples?: number;
}

export interface FineTuneJobResult {
  job_id: string;
  status: string;
  model?: string;
  example_count?: number;
}

export interface FineTuneJobStatus {
  id: string;
  status: string;
  model?: string;
  fine_tuned_model?: string | null;
  trained_tokens?: number;
  error?: string | null;
}

export const finetuneApi = api.injectEndpoints({
  endpoints: (builder) => ({
    exportDataset: builder.mutation<ExportDatasetResponse, { collection_id: string; max_examples?: number }>({
      query: (body) => ({ url: '/finetune/export', method: 'POST', body }),
    }),
    startFineTune: builder.mutation<FineTuneJobResult, StartFineTuneRequest>({
      query: (body) => ({ url: '/finetune/start', method: 'POST', body }),
    }),
    getFineTuneStatus: builder.query<FineTuneJobStatus, { job_id: string }>({
      query: ({ job_id }) => `/finetune/jobs/${job_id}`,
    }),
  }),
});

export const {
  useExportDatasetMutation,
  useStartFineTuneMutation,
  useGetFineTuneStatusQuery,
} = finetuneApi;
