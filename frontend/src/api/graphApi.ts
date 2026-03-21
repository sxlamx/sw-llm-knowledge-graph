import { api } from './baseApi';

export interface GraphNode {
  id: string;
  label: string;
  entity_type: string;
  description?: string;
  confidence: number;
  properties?: Record<string, string>;
  source_chunk_ids?: string[];
  topics?: string[];
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  weight: number;
  properties?: Record<string, string>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_nodes: number;
  total_edges: number;
}

export interface NodeDetail extends GraphNode {
  linked_chunks?: Array<{
    chunk_id: string;
    doc_id: string;
    doc_title: string;
    text: string;
    page?: number;
    has_image?: boolean;
    image_b64?: string;
  }>;
  neighbors?: GraphNode[];
}

export const graphApi = api.injectEndpoints({
  endpoints: (builder) => ({
    getGraphData: builder.query<GraphData, { collection_id: string; page?: number; depth?: number }>({
      query: ({ collection_id, page = 0, depth = 2 }) =>
        `/graph/subgraph?collection_id=${collection_id}&page=${page}&depth=${depth}`,
      providesTags: (_result, _error, { collection_id }) => [
        { type: 'GraphNode', id: collection_id },
      ],
    }),
    getGraphNode: builder.query<NodeDetail, { id: string; collection_id: string; depth?: number }>({
      query: ({ id, collection_id, depth = 1 }) =>
        `/graph/nodes/${id}?collection_id=${collection_id}&depth=${depth}`,
      providesTags: (_result, _error, { id }) => [{ type: 'GraphNode', id }],
    }),
    getGraphPath: builder.query<
      { path: string[]; nodes: GraphNode[]; edges: GraphEdge[] },
      { start_id: string; end_id: string; collection_id: string; max_hops?: number }
    >({
      query: ({ start_id, end_id, collection_id, max_hops = 10 }) =>
        `/graph/path?start_id=${start_id}&end_id=${end_id}&collection_id=${collection_id}&max_hops=${max_hops}`,
    }),
    updateGraphNode: builder.mutation<
      GraphNode,
      { id: string; collection_id: string; label?: string; description?: string; properties?: Record<string, string> }
    >({
      query: ({ id, ...body }) => ({ url: `/graph/nodes/${id}`, method: 'PUT', body }),
      invalidatesTags: (_result, _error, { id, collection_id }) => [
        { type: 'GraphNode', id },
        { type: 'GraphNode', id: collection_id },
      ],
    }),
    createGraphEdge: builder.mutation<
      GraphEdge,
      { collection_id: string; source: string; target: string; relation_type: string; weight?: number }
    >({
      query: (body) => ({ url: '/graph/edges', method: 'POST', body }),
      invalidatesTags: (_result, _error, { collection_id }) => [
        { type: 'GraphNode', id: collection_id },
      ],
    }),
    deleteGraphEdge: builder.mutation<void, { id: string; collection_id: string }>({
      query: ({ id }) => ({ url: `/graph/edges/${id}`, method: 'DELETE' }),
      invalidatesTags: (_result, _error, { collection_id }) => [
        { type: 'GraphNode', id: collection_id },
      ],
    }),
    exportGraph: builder.query<string, { collection_id: string; format: 'json' | 'graphml' }>({
      query: ({ collection_id, format }) =>
        `/graph/export?collection_id=${collection_id}&format=${format}`,
    }),
  }),
});

export const {
  useGetGraphDataQuery,
  useGetGraphNodeQuery,
  useGetGraphPathQuery,
  useUpdateGraphNodeMutation,
  useCreateGraphEdgeMutation,
  useDeleteGraphEdgeMutation,
  useLazyExportGraphQuery,
} = graphApi;
