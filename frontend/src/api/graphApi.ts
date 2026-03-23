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

export interface NodeSummary {
  node_id: string;
  summary: string;
  chunk_hash: string;
  updated_at?: number;
  from_cache: boolean;
}

export const graphApi = api.injectEndpoints({
  endpoints: (builder) => ({
    getGraphData: builder.query<GraphData, {
      collection_id: string;
      page?: number;
      depth?: number;
      date_from?: string;
      date_to?: string;
      doc_id?: string;
      entity_type_filters?: string[];
      ner_label_filters?: string[];
      ner_keyword_filters?: string[];
    }>({
      query: ({ collection_id, page = 0, depth = 2, date_from, date_to, doc_id, entity_type_filters, ner_label_filters, ner_keyword_filters }) => {
        let url = `/graph/subgraph?collection_id=${collection_id}&page=${page}&depth=${depth}`;
        if (date_from) url += `&date_from=${encodeURIComponent(date_from)}`;
        if (date_to) url += `&date_to=${encodeURIComponent(date_to)}`;
        if (doc_id) url += `&doc_id=${encodeURIComponent(doc_id)}`;
        if (entity_type_filters?.length) {
          entity_type_filters.forEach(f => { url += `&entity_type_filters=${encodeURIComponent(f)}`; });
        }
        if (ner_label_filters?.length) {
          ner_label_filters.forEach(f => { url += `&ner_label_filters=${encodeURIComponent(f)}`; });
        }
        if (ner_keyword_filters?.length) {
          ner_keyword_filters.forEach(f => { url += `&ner_keyword_filters=${encodeURIComponent(f)}`; });
        }
        return url;
      },
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
    getNodeSummary: builder.query<NodeSummary, { node_id: string; collection_id: string; force?: boolean }>({
      query: ({ node_id, collection_id, force = false }) =>
        `/graph/nodes/${node_id}/summary?collection_id=${collection_id}${force ? '&force=true' : ''}`,
      providesTags: (_result, _error, { node_id }) => [{ type: 'GraphNode', id: `summary-${node_id}` }],
    }),
    getNerKeywords: builder.query<
      Record<string, Array<{ text: string; count: number }>>,
      { collection_id: string; labels?: string[]; top_n?: number }
    >({
      query: ({ collection_id, labels = [], top_n = 30 }) => {
        let url = `/graph/ner-keywords?collection_id=${collection_id}&top_n=${top_n}`;
        labels.forEach(l => { url += `&labels=${encodeURIComponent(l)}`; });
        return url;
      },
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
  useGetNodeSummaryQuery,
  useGetNerKeywordsQuery,
} = graphApi;
