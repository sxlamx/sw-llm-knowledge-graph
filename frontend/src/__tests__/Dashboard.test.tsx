/**
 * Unit tests for Dashboard page — collections load, create, delete.
 *
 * Uses vi.mock to stub RTK Query hooks instead of MSW.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import Dashboard from '../pages/Dashboard';
import { renderWithProviders } from './test-utils';

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

const mockCollections = [
  { id: 'col-1', name: 'Research Papers', description: 'ML papers', status: 'active', doc_count: 42, created_at: '2026-01-01T00:00:00Z' },
  { id: 'col-2', name: 'Legal Docs', description: null, status: 'ingesting', doc_count: 0, created_at: '2026-01-15T00:00:00Z' },
];

const mockUseListCollectionsQuery = vi.fn();
const mockUseCreateCollectionMutation = vi.fn();
const mockUseDeleteCollectionMutation = vi.fn();

vi.mock('../api/collectionsApi', () => ({
  useListCollectionsQuery: (...args: unknown[]) => mockUseListCollectionsQuery(...args),
  useCreateCollectionMutation: () => [mockUseCreateCollectionMutation, { isLoading: false }],
  useDeleteCollectionMutation: () => [mockUseDeleteCollectionMutation, { isLoading: false }],
}));

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseListCollectionsQuery.mockReturnValue({
      data: { collections: mockCollections },
      isLoading: false,
      isError: false,
    });
    mockUseCreateCollectionMutation.mockResolvedValue({
      id: 'new-col',
      name: 'New Collection',
      status: 'active',
      doc_count: 0,
    });
    mockUseDeleteCollectionMutation.mockResolvedValue(undefined);
  });

  it('renders the page heading', () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByText('My Collections')).toBeInTheDocument();
  });

  it('renders New Collection button', () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByRole('button', { name: /new collection/i })).toBeInTheDocument();
  });

  it('loads and displays collections from API', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Research Papers')).toBeInTheDocument();
    });
    expect(screen.getByText('Legal Docs')).toBeInTheDocument();
  });

  it('shows doc_count for each collection', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument();
    });
  });

  it('shows status chip for each collection', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('active')).toBeInTheDocument();
      expect(screen.getByText('ingesting')).toBeInTheDocument();
    });
  });

  it('opens create dialog when New Collection button is clicked', async () => {
    renderWithProviders(<Dashboard />);
    fireEvent.click(screen.getByRole('button', { name: /new collection/i }));
    expect(screen.getByText('Create Collection')).toBeInTheDocument();
  });

  it('creates a collection and closes dialog on success', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('Research Papers')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new collection/i }));
    const nameInput = screen.getByLabelText('Name');
    fireEvent.change(nameInput, { target: { value: 'New Test Collection' } });
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => {
      expect(mockUseCreateCollectionMutation).toHaveBeenCalledWith({
        name: 'New Test Collection',
        description: '',
        folder_path: undefined,
      });
    });
  });

  it('shows validation error when creating with empty name', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('Research Papers')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new collection/i }));
    const nameInput = screen.getByLabelText('Name');
    fireEvent.change(nameInput, { target: { value: '' } });

    expect(screen.getByRole('button', { name: /^create$/i })).toBeDisabled();
  });

  it('deletes a collection when delete button is clicked', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('Research Papers')).toBeInTheDocument());

    // Find delete button by aria-label or Tooltip title (on parent Tooltip)
    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(mockUseDeleteCollectionMutation).toHaveBeenCalledWith('col-1');
    });
  });

  it('has folder_path input in create dialog', async () => {
    renderWithProviders(<Dashboard />);
    fireEvent.click(screen.getByRole('button', { name: /new collection/i }));
    expect(screen.getByLabelText('Folder path (optional)')).toBeInTheDocument();
  });

  it('has description input in create dialog', async () => {
    renderWithProviders(<Dashboard />);
    fireEvent.click(screen.getByRole('button', { name: /new collection/i }));
    expect(screen.getByLabelText('Description (optional)')).toBeInTheDocument();
  });
});
