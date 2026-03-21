import { api } from './baseApi';

export interface EntityTypeDef {
  description?: string;
  parent?: string;
  examples?: string[];
}

export interface RelationTypeDef {
  domain: string[];
  range: string[];
  description?: string;
}

export interface Ontology {
  collection_id: string;
  version: number;
  entity_types: Record<string, EntityTypeDef>;
  relationship_types: Record<string, RelationTypeDef>;
  updated_at?: string;
}

export const ontologyApi = api.injectEndpoints({
  endpoints: (builder) => ({
    getOntology: builder.query<Ontology, { collection_id: string }>({
      query: ({ collection_id }) => `/ontology?collection_id=${collection_id}`,
      providesTags: (_result, _error, { collection_id }) => [
        { type: 'Ontology', id: collection_id },
      ],
    }),
    updateOntology: builder.mutation<
      Ontology,
      { collection_id: string; entity_types?: Record<string, EntityTypeDef>; relationship_types?: Record<string, RelationTypeDef> }
    >({
      query: ({ collection_id, ...body }) => ({
        url: `/ontology?collection_id=${collection_id}`,
        method: 'PUT',
        body,
      }),
      invalidatesTags: (_result, _error, { collection_id }) => [
        { type: 'Ontology', id: collection_id },
      ],
    }),
    generateOntology: builder.mutation<Ontology, { collection_id: string }>({
      query: (body) => ({ url: '/ontology/generate', method: 'POST', body }),
      invalidatesTags: (_result, _error, { collection_id }) => [
        { type: 'Ontology', id: collection_id },
      ],
    }),
  }),
});

export const {
  useGetOntologyQuery,
  useUpdateOntologyMutation,
  useGenerateOntologyMutation,
} = ontologyApi;
