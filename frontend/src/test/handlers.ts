/**
 * MSW request handlers — shared across component and integration tests.
 * These intercept API calls and return fixture data without hitting the real network.
 */
import { http, HttpResponse } from 'msw';
import type { User } from '../types/api';

export const mockUser: User = {
  id: 'test-user-id',
  email: 'test@example.com',
  name: 'Test User',
  avatar_url: 'https://example.com/avatar.png',
};

export const mockCollections = [
  { id: 'col-1', name: 'Test Collection', description: 'A test collection', status: 'active', doc_count: 5, created_at: '2026-01-01T00:00:00Z' },
  { id: 'col-2', name: 'Second Collection', description: null, status: 'active', doc_count: 10, created_at: '2026-01-15T00:00:00Z' },
];

export const mockGraphNodes = [
  { id: 'n1', label: 'Alice', entity_type: 'PERSON', description: 'A person', confidence: 0.9, properties: {}, source_chunk_ids: [], topics: [] },
  { id: 'n2', label: 'OpenAI', entity_type: 'ORGANIZATION', description: 'An AI org', confidence: 0.95, properties: {}, source_chunk_ids: [], topics: [] },
  { id: 'n3', label: 'San Francisco', entity_type: 'LOCATION', description: 'A city', confidence: 0.88, properties: {}, source_chunk_ids: [], topics: [] },
];

export const mockGraphEdges = [
  { id: 'e1', source: 'n1', target: 'n2', relation_type: 'WORKS_AT', weight: 0.8, properties: {} },
  { id: 'e2', source: 'n2', target: 'n3', relation_type: 'LOCATED_IN', weight: 0.9, properties: {} },
];

export const mockGraphData = {
  nodes: mockGraphNodes,
  edges: mockGraphEdges,
  total_nodes: 3,
  total_edges: 2,
};

export const mockSearchResults = [
  {
    id: 'r1',
    chunk_id: 'c1',
    doc_id: 'd1',
    doc_title: 'Attention Is All You Need',
    text: 'The Transformer model uses self-attention mechanisms.',
    score: 0.85,
    final_score: 0.85,
    vector_score: 0.9,
    keyword_score: 0.78,
    page: 3,
    highlights: ['Transformer', 'self-attention'],
    topics: ['machine learning'],
  },
  {
    id: 'r2',
    chunk_id: 'c2',
    doc_id: 'd2',
    doc_title: 'BERT Paper',
    text: 'Bidirectional encoder representations from transformers.',
    score: 0.72,
    final_score: 0.72,
    vector_score: 0.75,
    keyword_score: 0.68,
    page: 1,
    highlights: ['bidirectional', 'transformers'],
    topics: ['NLP'],
  },
];

export const handlers = [
  http.get('/api/v1/collections', () =>
    HttpResponse.json({ collections: mockCollections })
  ),

  http.get('/api/v1/collections/:id', ({ params }) => {
    const col = mockCollections.find(c => c.id === params.id);
    if (!col) return HttpResponse.json({ error: 'Not found' }, { status: 404 });
    return HttpResponse.json(col);
  }),

  http.post('/api/v1/collections', async ({ request }) => {
    const body = await request.json() as { name: string; description?: string; folder_path?: string };
    return HttpResponse.json(
      { id: `new-${Date.now()}`, name: body.name, description: body.description, status: 'active', doc_count: 0 },
      { status: 201 }
    );
  }),

  http.delete('/api/v1/collections/:id', () =>
    HttpResponse.json(null, { status: 204 })
  ),

  http.get('/api/v1/graph/subgraph', () =>
    HttpResponse.json(mockGraphData)
  ),

  http.get('/api/v1/graph/nodes/:id', ({ params }) => {
    const node = mockGraphNodes.find(n => n.id === params.id);
    if (!node) return HttpResponse.json({ error: 'Not found' }, { status: 404 });
    return HttpResponse.json({
      node,
      linked_chunks: [
        { chunk_id: 'c1', doc_id: 'd1', doc_title: 'Test Doc', text: 'Sample text...' },
      ],
    });
  }),

  http.post('/api/v1/search', async ({ request }) => {
    const body = await request.json() as { mode?: string; limit?: number };
    return HttpResponse.json({
      results: mockSearchResults.slice(0, body.limit ?? 20),
      total: mockSearchResults.length,
      latency_ms: 42,
      search_mode: body.mode ?? 'hybrid',
    });
  }),

  http.get('/api/v1/search/suggestions', () =>
    HttpResponse.json({ suggestions: ['transformer', 'attention', 'BERT'] })
  ),

  http.get('/api/v1/topics', () =>
    HttpResponse.json({
      topics: [
        { id: 't1', name: 'Machine Learning', keywords: ['ML', 'neural'], frequency: 100, score: 0.9 },
        { id: 't2', name: 'NLP', keywords: ['language', 'transformer'], frequency: 80, score: 0.85 },
      ],
    })
  ),

  http.post('/api/v1/auth/refresh', () =>
    HttpResponse.json({ access_token: 'refreshed-token', expires_in: 600 })
  ),

  http.post('/api/v1/auth/google', () =>
    HttpResponse.json({ access_token: 'test-token', token_type: 'bearer', expires_in: 600, user: mockUser })
  ),

  http.get('/api/v1/ingest/jobs/:id', () =>
    HttpResponse.json({
      id: 'job-1',
      job_id: 'job-1',
      collection_id: 'col-1',
      status: 'running',
      progress: 0.5,
      total_docs: 10,
      processed_docs: 5,
    })
  ),

  http.get('/api/v1/documents', () =>
    HttpResponse.json({
      documents: [
        { id: 'd1', title: 'Test Doc 1', file_type: 'pdf', chunk_count: 5, status: 'indexed' },
        { id: 'd2', title: 'Test Doc 2', file_type: 'docx', chunk_count: 3, status: 'indexed' },
      ],
      total: 2,
    })
  ),

  http.get('/api/v1/analytics/summary', () =>
    HttpResponse.json({
      collection_id: 'col-1',
      node_count: 100,
      edge_count: 250,
      num_communities: 5,
      top_pagerank: [{ id: 'n1', label: 'Alice', score: 0.7 }],
      top_betweenness: [{ id: 'n2', label: 'Bob', score: 0.5 }],
    })
  ),
];
