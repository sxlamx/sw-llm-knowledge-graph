import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router-dom';
import Collection from '../pages/Collection';
import { renderWithProviders } from './test-utils';

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

const mockCollection = {
  id: 'col-1',
  name: 'Test Collection',
  description: 'A test collection',
  status: 'active',
  doc_count: 2,
};

const mockDocuments = {
  documents: [
    { id: 'doc-1', title: 'Document One', file_type: 'pdf', chunk_count: 5, status: 'indexed' },
    { id: 'doc-2', title: 'Document Two', file_type: 'docx', chunk_count: 3, status: 'indexed' },
  ],
  total: 2,
};

const mockUseGetCollectionQuery = vi.fn();
const mockUseListDocumentsQuery = vi.fn();
const mockDeleteDocument = vi.fn();
const mockUseTriggerNerPassMutation = vi.fn();

vi.mock('../api/collectionsApi', () => ({
  useGetCollectionQuery: (...args: unknown[]) => mockUseGetCollectionQuery(...args),
  useListCollectionsQuery: () => ({ data: { collections: [] } }),
}));

vi.mock('../api/documentsApi', () => ({
  useListDocumentsQuery: (...args: unknown[]) => mockUseListDocumentsQuery(...args),
  useDeleteDocumentMutation: () => [mockDeleteDocument, { isLoading: false }],
}));

vi.mock('../components/ingest/IngestPanel', () => ({
  default: () => null,
}));

vi.mock('../api/ingestApi', () => ({
  useTriggerNerPassMutation: () => [mockUseTriggerNerPassMutation, { isLoading: false }],
}));

function renderCollection() {
  return renderWithProviders(
    <Routes>
      <Route path="/collection/:id" element={<Collection />} />
    </Routes>,
    { initialEntries: ['/collection/col-1'] },
  );
}

describe('Collection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseGetCollectionQuery.mockReturnValue({ data: mockCollection, isLoading: false });
    mockDeleteDocument.mockReset();
    mockUseListDocumentsQuery.mockReturnValue({ data: mockDocuments, isLoading: false });
    mockDeleteDocument.mockImplementation(() => ({ unwrap: () => Promise.resolve(undefined) }));
  });

  it('shows confirmation dialog before document deletion', async () => {
    renderCollection();
    await waitFor(() => {
      expect(screen.getByText('Document One')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole('button', { name: /delete document/i });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Delete Document')).toBeInTheDocument();
    });
    expect(screen.getByText(/permanently delete the document/i)).toBeInTheDocument();
  });

  it('dispatches delete when confirmed in dialog', async () => {
    renderCollection();
    await waitFor(() => {
      expect(screen.getByText('Document One')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole('button', { name: /delete document/i });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Delete Document')).toBeInTheDocument();
    });

    const confirmButton = screen.getByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockDeleteDocument).toHaveBeenCalledWith({
        doc_id: 'doc-1',
        collection_id: 'col-1',
      });
    });
  });

  it('closes dialog on Cancel without deleting', async () => {
    renderCollection();
    await waitFor(() => {
      expect(screen.getByText('Document One')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole('button', { name: /delete document/i });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Delete Document')).toBeInTheDocument();
    });

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(screen.queryByText('Delete Document')).not.toBeInTheDocument();
    });

    expect(mockDeleteDocument).not.toHaveBeenCalled();
  });
});