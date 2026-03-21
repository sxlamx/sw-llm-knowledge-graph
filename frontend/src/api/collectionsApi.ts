import { api } from './baseApi';

export interface Collection {
  id: string;
  name: string;
  description?: string;
  folder_path?: string;
  status: string;
  doc_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface CollectionCreate {
  name: string;
  description?: string;
  folder_path?: string;
}

export const collectionsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listCollections: builder.query<{ collections: Collection[] }, void>({
      query: () => '/collections',
      providesTags: ['Collection'],
    }),
    getCollection: builder.query<Collection, string>({
      query: (id) => `/collections/${id}`,
      providesTags: (_result, _error, id) => [{ type: 'Collection', id }],
    }),
    createCollection: builder.mutation<Collection, CollectionCreate>({
      query: (body) => ({ url: '/collections', method: 'POST', body }),
      invalidatesTags: ['Collection'],
    }),
    deleteCollection: builder.mutation<void, string>({
      query: (id) => ({ url: `/collections/${id}`, method: 'DELETE' }),
      invalidatesTags: ['Collection'],
    }),
  }),
});

export const {
  useListCollectionsQuery,
  useGetCollectionQuery,
  useCreateCollectionMutation,
  useDeleteCollectionMutation,
} = collectionsApi;
