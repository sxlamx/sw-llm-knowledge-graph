/**
 * Unit tests for SearchPage — search modes and UI elements.
 *
 * Uses vi.mock to stub RTK Query hooks.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import SearchPage from '../pages/SearchPage';
import { renderWithProviders } from './test-utils';

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

vi.mock('../api/collectionsApi', () => ({
  useListCollectionsQuery: () => ({
    data: {
      collections: [{ id: 'col-1', name: 'Test Collection', doc_count: 5, status: 'active' }],
    },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock('../api/searchApi', () => ({
  useSearchMutation: () => [
    async () => ({
      results: [],
      total: 0,
      latency_ms: 10,
      search_mode: 'vector',
    }),
    { isLoading: false, error: undefined },
  ],
}));

describe('SearchPage', () => {
  it('renders search input', () => {
    renderWithProviders(<SearchPage />, { initialEntries: ['/search'] });
    expect(screen.getByPlaceholderText(/enter your query/i)).toBeInTheDocument();
  });

  it('renders Search button', () => {
    renderWithProviders(<SearchPage />, { initialEntries: ['/search'] });
    expect(screen.getByRole('button', { name: /search/i })).toBeInTheDocument();
  });

  it('renders all four search mode toggle buttons', () => {
    renderWithProviders(<SearchPage />, { initialEntries: ['/search'] });
    expect(screen.getByText('Vector')).toBeInTheDocument();
    expect(screen.getByText('Keyword')).toBeInTheDocument();
    expect(screen.getByText('Hybrid')).toBeInTheDocument();
    expect(screen.getByText('Graph')).toBeInTheDocument();
  });

  it('renders the Search heading', () => {
    renderWithProviders(<SearchPage />, { initialEntries: ['/search'] });
    expect(screen.getByRole('heading', { name: 'Search' })).toBeInTheDocument();
  });
});
