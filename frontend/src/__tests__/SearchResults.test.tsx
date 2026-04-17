import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import SearchResults from '../components/search/SearchResults';
import { renderWithProviders } from './test-utils';

const mockResults = [
  { id: 'r1', chunk_id: 'c1', doc_title: 'Doc 1', text: 'Result one text', score: 0.9, page: 1 },
  { id: 'r2', chunk_id: 'c2', doc_title: 'Doc 2', text: 'Result two text', score: 0.7, page: 2 },
  { id: 'r3', chunk_id: 'c3', doc_title: 'Doc 3', text: 'Result three text', score: 0.5 },
];

describe('SearchResults', () => {
  it('renders results list with result count', () => {
    renderWithProviders(<SearchResults results={mockResults} />);
    expect(screen.getByText(/3 results/)).toBeInTheDocument();
  });

  it('renders each result card', () => {
    renderWithProviders(<SearchResults results={mockResults} />);
    expect(screen.getByText('Doc 1')).toBeInTheDocument();
    expect(screen.getByText('Doc 2')).toBeInTheDocument();
    expect(screen.getByText('Doc 3')).toBeInTheDocument();
  });

  it('shows empty state when no results', () => {
    renderWithProviders(<SearchResults results={[]} />);
    expect(screen.getByText('No results found.')).toBeInTheDocument();
  });

  it('shows loading spinner', () => {
    renderWithProviders(<SearchResults results={[]} loading={true} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows latency when provided', () => {
    renderWithProviders(<SearchResults results={mockResults} latencyMs={42} />);
    expect(screen.getByText(/42ms/)).toBeInTheDocument();
  });

  it('shows load more button when hasMore is true', () => {
    const onLoadMore = vi.fn();
    renderWithProviders(<SearchResults results={mockResults} hasMore={true} onLoadMore={onLoadMore} />);
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
  });

  it('does not show load more button when hasMore is false', () => {
    renderWithProviders(<SearchResults results={mockResults} hasMore={false} />);
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument();
  });

  it('calls onLoadMore when load more is clicked', () => {
    const onLoadMore = vi.fn();
    renderWithProviders(<SearchResults results={mockResults} hasMore={true} onLoadMore={onLoadMore} />);
    fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it('renders without latency when not provided', () => {
    renderWithProviders(<SearchResults results={mockResults} />);
    expect(screen.getByText(/3 results$/)).toBeInTheDocument();
  });
});