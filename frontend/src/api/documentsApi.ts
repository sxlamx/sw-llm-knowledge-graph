import { api } from './baseApi';
import type { DocumentListResponse } from '../types/api';

export const documentsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listDocuments: builder.query<DocumentListResponse, { collection_id: string; limit?: number; offset?: number }>({
      query: ({ collection_id, limit = 50, offset = 0 }) =>
        `/documents?collection_id=${collection_id}&limit=${limit}&offset=${offset}`,
      providesTags: ['Document'],
    }),
    deleteDocument: builder.mutation<void, { doc_id: string; collection_id: string }>({
      query: ({ doc_id, collection_id }) => ({
        url: `/documents/${doc_id}?collection_id=${collection_id}`,
        method: 'DELETE',
      }),
      invalidatesTags: ['Document'],
    }),
  }),
});

export const { useListDocumentsQuery, useDeleteDocumentMutation } = documentsApi;
