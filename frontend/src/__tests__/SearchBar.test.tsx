import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import SearchBar from '../components/search/SearchBar';
import { renderWithProviders } from './test-utils';

// Stub RTK Query hook — no real API needed
vi.mock('../api/searchApi', () => ({
  useGetSearchSuggestionsQuery: () => ({ data: { suggestions: ['suggestion-one', 'suggestion-two'] } }),
}));

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
}));

describe('SearchBar', () => {
  it('renders the search input', () => {
    renderWithProviders(<SearchBar />);
    expect(screen.getByLabelText(/search knowledge graph/i)).toBeInTheDocument();
  });

  it('renders all four mode toggle buttons', () => {
    renderWithProviders(<SearchBar />);
    expect(screen.getByText('Hybrid')).toBeInTheDocument();
    expect(screen.getByText('Vector')).toBeInTheDocument();
    expect(screen.getByText('BM25')).toBeInTheDocument();
    expect(screen.getByText('Graph')).toBeInTheDocument();
  });

  it('dispatches setSearchMode when a mode is clicked', () => {
    const { store } = renderWithProviders(<SearchBar />);
    fireEvent.click(screen.getByText('Vector'));
    expect(store.getState().search.mode).toBe('vector');
  });

  it('default mode is hybrid', () => {
    const { store } = renderWithProviders(<SearchBar />);
    expect(store.getState().search.mode).toBe('hybrid');
  });

  it('dispatches setSearchQuery and navigates on Enter', () => {
    const { store } = renderWithProviders(<SearchBar />, {
      initialEntries: ['/search'],
    });
    const input = screen.getByLabelText(/search knowledge graph/i);
    fireEvent.change(input, { target: { value: 'tax law' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(store.getState().search.query).toBe('tax law');
  });

  it('does not navigate on empty query', () => {
    const { store } = renderWithProviders(<SearchBar />);
    const input = screen.getByLabelText(/search knowledge graph/i);
    fireEvent.keyDown(input, { key: 'Enter' });
    // query should still be empty
    expect(store.getState().search.query).toBe('');
  });
});
