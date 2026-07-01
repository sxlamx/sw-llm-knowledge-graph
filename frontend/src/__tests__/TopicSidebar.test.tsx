/**
 * Unit tests for TopicSidebar — API wiring and topic selection.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import TopicSidebar from '../components/search/TopicSidebar';
import { renderWithProviders } from './test-utils';

const mockTopics = [
  { id: 't1', name: 'Machine Learning', keywords: ['ml', 'ai'], frequency: 10, score: 0.9 },
  { id: 't2', name: 'Legal', keywords: ['law', 'court'], frequency: 5, score: 0.7 },
];

const mockUseListTopicsQuery = vi.fn();

vi.mock('../api/topicsApi', () => ({
  useListTopicsQuery: (...args: unknown[]) => mockUseListTopicsQuery(...args),
}));

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
}));

describe('TopicSidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "Select a collection" when no collectionId provided', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: undefined, isLoading: false });
    renderWithProviders(<TopicSidebar collectionId={null} />);
    expect(screen.getByText('Select a collection to see topics.')).toBeInTheDocument();
  });

  it('shows loading skeletons when loading', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: undefined, isLoading: true });
    renderWithProviders(<TopicSidebar collectionId="col-1" />);
    expect(screen.getByText('Topics')).toBeInTheDocument();
  });

  it('renders topics from API response', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: { topics: mockTopics }, isLoading: false });
    renderWithProviders(<TopicSidebar collectionId="col-1" />);
    expect(screen.getByText('Machine Learning')).toBeInTheDocument();
    expect(screen.getByText('Legal')).toBeInTheDocument();
  });

  it('shows "No topics found" when API returns empty list', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: { topics: [] }, isLoading: false });
    renderWithProviders(<TopicSidebar collectionId="col-1" />);
    expect(screen.getByText('No topics found.')).toBeInTheDocument();
  });

  it('dispatches setSelectedTopics when topic checkbox is clicked', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: { topics: mockTopics }, isLoading: false });
    const { store } = renderWithProviders(<TopicSidebar collectionId="col-1" />);

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);

    expect(store.getState().search.selectedTopics).toContain('Machine Learning');
  });

  it('calls useListTopicsQuery with correct collectionId', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: { topics: [] }, isLoading: false });
    renderWithProviders(<TopicSidebar collectionId="specific-col" />);

    expect(mockUseListTopicsQuery).toHaveBeenCalledWith(
      { collection_id: 'specific-col', limit: 100 },
      { skip: false }
    );
  });

  it('skips API call when collectionId is null', () => {
    mockUseListTopicsQuery.mockReturnValue({ data: undefined, isLoading: false });
    renderWithProviders(<TopicSidebar collectionId={null} />);

    expect(mockUseListTopicsQuery).toHaveBeenCalledWith(
      expect.anything(),
      { skip: true }
    );
  });
});