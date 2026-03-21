import { api } from './baseApi';

export interface Document {
  id: string;
  collection_id: string;
  title: string;
  file_path?: string;
  file_type?: string;
  size_bytes?: number;
  chunk_count: number;
  status: string;
  created_at?: string;
}

export const documentsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listDocuments: builder.query<
      { documents: Document[]; total: number },
      { collection_id: string; limit?: number; offset?: number }
    >({
      query: ({ collection_id, limit = 50, offset = 0 }) =>
        `/documents?collection_id=${collection_id}&limit=${limit}&offset=${offset}`,
      providesTags: ['Document'],
    }),
    getDocument: builder.query<Document, string>({
      query: (id) => `/documents/${id}`,
      providesTags: (_result, _error, id) => [{ type: 'Document', id }],
    }),
    deleteDocument: builder.mutation<void, { id: string; collection_id: string }>({
      query: ({ id }) => ({ url: `/documents/${id}`, method: 'DELETE' }),
      invalidatesTags: ['Document', 'Collection'],
    }),
  }),
});

export const { useListDocumentsQuery, useGetDocumentQuery, useDeleteDocumentMutation } =
  documentsApi;
