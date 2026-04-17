import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import Settings from '../pages/Settings';
import { renderWithProviders } from './test-utils';

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
})();

Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock });

vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsConnect: () => ({ type: 'ws/connect' }),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

const mockLogoutUser = vi.fn();
vi.mock('../api/authApi', () => ({
  useLogoutUserMutation: () => [mockLogoutUser, { isLoading: false }],
}));

describe('Settings Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLogoutUser.mockResolvedValue(undefined);
    localStorageMock.clear();
  });

  it('renders Settings heading', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByText('Settings')).toBeInTheDocument();
  });

  it('renders user profile info when user is present', () => {
    renderWithProviders(<Settings />, {
      preloadedState: {
        auth: {
          user: { id: 'u1', email: 'test@example.com', name: 'Test User', picture: undefined },
          accessToken: 'tok',
          isAuthenticated: true,
          isLoading: false,
        },
      },
    });
    expect(screen.getByText('Test User')).toBeInTheDocument();
    expect(screen.getByText('test@example.com')).toBeInTheDocument();
  });

  it('shows Unknown when user is null', () => {
    renderWithProviders(<Settings />, {
      preloadedState: {
        auth: { user: null, accessToken: null, isAuthenticated: false, isLoading: false },
      },
    });
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  it('has dark mode toggle', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
    expect(screen.getByText('Dark mode')).toBeInTheDocument();
  });

  it('dark mode toggle reflects current theme', () => {
    renderWithProviders(<Settings />, {
      preloadedState: {
        ui: { drawerOpen: false, sidebarOpen: true, themeMode: 'dark', snackbar: { open: false, message: '', severity: 'info' }, jobStatuses: {} },
      },
    });
    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('toggling dark mode dispatches toggleTheme', () => {
    const { store } = renderWithProviders(<Settings />);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(store.getState().ui.themeMode).toBe('dark');
  });

  it('has logout button', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument();
  });

  it('clicking sign out dispatches logout', () => {
    const { store } = renderWithProviders(<Settings />);
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }));
    expect(store.getState().auth.isAuthenticated).toBe(false);
  });

  it('renders Profile section', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByText('Profile')).toBeInTheDocument();
  });

  it('renders Appearance section', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByText('Appearance')).toBeInTheDocument();
  });

  it('renders Account section', () => {
    renderWithProviders(<Settings />);
    expect(screen.getByText('Account')).toBeInTheDocument();
  });
});