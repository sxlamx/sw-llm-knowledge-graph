import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import IngestPanel from '../components/ingest/IngestPanel';
import { renderWithProviders } from './test-utils';

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

const mockStartIngest = vi.fn();
vi.mock('../api/ingestApi', () => ({
  useStartIngestJobMutation: () => [mockStartIngest, { isLoading: false }],
  ingestApi: { endpoints: {}, reducerPath: 'ingestApi' },
}));

vi.mock('../api/templatesApi', () => ({
  useListTemplatesQuery: () => ({ data: [], isLoading: false }),
  useListExtractionMethodsQuery: () => ({ data: [] }),
  templatesApi: { endpoints: {}, reducerPath: 'templatesApi' },
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }
}

describe('IngestPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStartIngest.mockReset();
    MockEventSource.instances = [];
    (globalThis as Record<string, unknown>).EventSource = MockEventSource;
  });

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).EventSource;
  });

  it('renders heading', () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    expect(screen.getByText('Ingest Documents')).toBeInTheDocument();
  });

  it('renders folder path input', () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    expect(screen.getByLabelText('Folder path')).toBeInTheDocument();
  });

  it('renders start ingest button', () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    expect(screen.getByRole('button', { name: /start ingest/i })).toBeInTheDocument();
  });

  it('renders advanced options accordion', () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    expect(screen.getByText('Advanced options')).toBeInTheDocument();
  });

  it('shows template info in advanced options when expanded', async () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    fireEvent.click(screen.getByText('Advanced options'));
    await waitFor(() => {
      expect(screen.getByText('No templates available')).toBeInTheDocument();
    });
  });

  it('shows warning via store when starting with empty folder path', () => {
    const { store } = renderWithProviders(<IngestPanel collectionId="col-1" />);
    fireEvent.click(screen.getByRole('button', { name: /start ingest/i }));
    const state = store.getState();
    expect(state.ui.snackbar.open).toBe(true);
    expect(state.ui.snackbar.message).toBe('Please enter or select a folder path.');
  });

  it('calls startIngest with folder path when form is filled', async () => {
    mockStartIngest.mockReturnValue({ unwrap: () => Promise.resolve({ job_id: 'j1' }) });
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    const input = screen.getByLabelText('Folder path');
    fireEvent.change(input, { target: { value: '/tmp/docs' } });
    fireEvent.click(screen.getByRole('button', { name: /start ingest/i }));
    await waitFor(() => {
      expect(mockStartIngest).toHaveBeenCalledWith(
        expect.objectContaining({
          collection_id: 'col-1',
          folder_path: '/tmp/docs',
        }),
      );
    });
  });

  it('renders entity extraction toggle in advanced options', async () => {
    renderWithProviders(<IngestPanel collectionId="col-1" />);
    fireEvent.click(screen.getByText('Advanced options'));
    await waitFor(() => {
      expect(screen.getByText('Extract entities & relations')).toBeInTheDocument();
    });
  });
});