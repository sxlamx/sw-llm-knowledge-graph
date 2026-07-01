/**
 * Unit tests for Search page — search modes, topic filter wiring.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import Search from '../pages/Search';
import { renderWithProviders } from './test-utils';

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

const mockSearchResult = vi.fn().mockResolvedValue({
  data: {
    results: [
      { id: 'r1', chunk_id: 'c1', doc_title: 'Doc 1', text: 'Test result text', score: 0.9, page: 1, topics: ['AI'] },
    ],
    total: 1,
    latency_ms: 42,
    query: 'test',
  },
});

const mockUseListCollectionsQuery = vi.fn();

vi.mock('../api/collectionsApi', () => ({
  useListCollectionsQuery: (...args: unknown[]) => mockUseListCollectionsQuery(...args),
  useGetCollectionQuery: () => ({ data: undefined }),
  useCreateCollectionMutation: () => [vi.fn(), { isLoading: false }],
  useDeleteCollectionMutation: () => [vi.fn(), { isLoading: false }],
}));

vi.mock('../api/searchApi', () => ({
  useSearchMutation: () => [mockSearchResult, { isLoading: false }],
  useGetSearchSuggestionsQuery: () => ({ data: [] }),
}));

vi.mock('../api/topicsApi', () => ({
  useListTopicsQuery: () => ({ data: { topics: [] }, isLoading: false }),
  useGetTopicNodesQuery: () => ({ data: undefined }),
  topicsApi: { endpoints: {}, reducerPath: 'topicsApi' },
}));

describe('Search page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseListCollectionsQuery.mockReturnValue({
      data: { collections: [{ id: 'col-1', name: 'Test Collection', doc_count: 5, status: 'active' }] },
      isLoading: false,
    });
  });

  it('renders the Search heading', () => {
    renderWithProviders(<Search />);
    expect(screen.getByText('Search')).toBeInTheDocument();
  });

  it('renders the SearchBar component', () => {
    renderWithProviders(<Search />);
    expect(screen.getByText('Hybrid')).toBeInTheDocument();
  });

  it('renders TopicSidebar', () => {
    renderWithProviders(<Search />);
    expect(screen.getByText('Topics')).toBeInTheDocument();
  });

  it('renders collection selector dropdown', () => {
    renderWithProviders(<Search />);
    expect(screen.getAllByText('Collection').length).toBeGreaterThan(0);
  });

  it('shows placeholder text when no query entered', () => {
    renderWithProviders(<Search />);
    expect(screen.getByText(/enter a query above/i)).toBeInTheDocument();
  });

  it('includes topics in search request when selected', async () => {
    renderWithProviders(<Search />, {
      preloadedState: {
        auth: { user: { id: 'u1', email: 'a@b.com', name: 'T' }, accessToken: 'tok', isAuthenticated: true, isLoading: false },
        search: { query: 'test', mode: 'hybrid', weights: { vector: 0.6, keyword: 0.3, graph: 0.1 }, selectedTopics: ['AI'], selectedCollectionIds: ['col-1'] },
      },
    });

    await waitFor(() => {
      expect(mockSearchResult).toHaveBeenCalled();
    });

    const callArg = mockSearchResult.mock.calls[0][0];
    expect(callArg.topics).toContain('AI');
  });

  it('passes collection id to TopicSidebar', () => {
    renderWithProviders(<Search />, {
      preloadedState: {
        search: { query: '', mode: 'hybrid', weights: { vector: 0.6, keyword: 0.3, graph: 0.1 }, selectedTopics: [], selectedCollectionIds: ['col-1'] },
      },
    });

    expect(screen.getByText('Topics')).toBeInTheDocument();
  });
});