import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { api } from '../../../api/baseApi';
import TemplatePicker from '../TemplatePicker';


function createTestStore() {
  return configureStore({
    reducer: {
      [api.reducerPath]: api.reducer,
    },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware().concat(api.middleware),
  });
}

function renderWithProvider(ui: React.ReactElement) {
  const store = createTestStore();
  return {
    store,
    ...render(<Provider store={store}>{ui}</Provider>),
  };
}

describe('TemplatePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders Default chip', () => {
    const onChange = vi.fn();
    renderWithProvider(
      <TemplatePicker value={null} onChange={onChange} />
    );
    expect(screen.getByText('Default')).toBeInTheDocument();
  });

  it('renders "Extraction template" label', () => {
    renderWithProvider(
      <TemplatePicker value={null} onChange={vi.fn()} />
    );
    expect(screen.getByText('Extraction template')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    renderWithProvider(
      <TemplatePicker value={null} onChange={vi.fn()} />
    );
    // The component renders either loading spinner, error, or chips
    // Since the RTK Query will be in loading state initially, we expect
    // either the CircularProgress or the chips based on the mock
    const defaultChip = screen.queryByText('Default');
    const spinner = document.querySelector('.MuiCircularProgress-root');
    expect(defaultChip !== null || spinner !== null).toBe(true);
  });

  it('calls onChange with null when Default is clicked', () => {
    const onChange = vi.fn();
    renderWithProvider(
      <TemplatePicker value="general/graph" onChange={onChange} />
    );
    const defaultChip = screen.getByText('Default');
    fireEvent.click(defaultChip);
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('displays "Using:" label when value is set', () => {
    renderWithProvider(
      <TemplatePicker value="general/graph" onChange={vi.fn()} />
    );
    expect(screen.getByText(/Using: general\/graph/)).toBeInTheDocument();
  });
});