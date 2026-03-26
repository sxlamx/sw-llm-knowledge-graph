import { api } from './baseApi';
import type { Collection, CollectionCreate } from '../types/api';

export type { Collection };

export const collectionsApi = api.injectEndpoints({
  endpoints: (builder) => ({
    listCollections: builder.query<{ collections: Collection[] }, void>({
      query: () => '/collections',
      providesTags: ['Collection'],
    }),
    getCollection: builder.query<Collection, string>({
      query: (id) => `/collections/${id}`,
      providesTags: (_r, _e, id) => [{ type: 'Collection', id }],
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
