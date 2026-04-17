import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { ErrorBoundary } from '../components/common/ErrorBoundary';
import { renderWithProviders } from './test-utils';

const ThrowError = () => {
  throw new Error('test error');
};

const WorkingChild = () => <div>Working content</div>;

describe('ErrorBoundary', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders children when no error', () => {
    renderWithProviders(
      <ErrorBoundary>
        <WorkingChild />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Working content')).toBeInTheDocument();
  });

  it('renders fallback UI on error', () => {
    renderWithProviders(
      <ErrorBoundary>
        <ThrowError />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('shows error message in fallback', () => {
    renderWithProviders(
      <ErrorBoundary>
        <ThrowError />
      </ErrorBoundary>,
    );
    expect(screen.getByText('test error')).toBeInTheDocument();
  });

  it('shows reload button', () => {
    renderWithProviders(
      <ErrorBoundary>
        <ThrowError />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('button', { name: /reload page/i })).toBeInTheDocument();
  });

  it('shows fallback when error has no message', () => {
    const ThrowNullError = () => {
      throw { message: null };
    };
    renderWithProviders(
      <ErrorBoundary>
        <ThrowNullError />
      </ErrorBoundary>,
    );
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument();
  });
});