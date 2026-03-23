import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import NavBar from '../components/common/NavBar';
import { renderWithProviders } from './test-utils';

// RTK Query mutations need to be mocked because we don't have a real API
vi.mock('../api/authApi', () => ({
  useLogoutUserMutation: () => [vi.fn().mockResolvedValue({})],
}));

// wsMiddleware tries to create a WebSocket; stub it
vi.mock('../store/wsMiddleware', () => ({
  wsMiddleware: () => (next: (a: unknown) => unknown) => (action: unknown) => next(action),
  wsDisconnect: () => ({ type: 'ws/disconnect' }),
}));

describe('NavBar', () => {
  it('renders brand title', () => {
    renderWithProviders(<NavBar />);
    expect(screen.getByText('Knowledge Graph Builder')).toBeInTheDocument();
  });

  it('does not show Logout when no user', () => {
    renderWithProviders(<NavBar />);
    expect(screen.queryByText('Logout')).not.toBeInTheDocument();
  });

  it('shows Logout when user is logged in', () => {
    renderWithProviders(<NavBar />, {
      preloadedState: {
        auth: {
          user: { id: 'u1', email: 'a@b.com', name: 'Alice', picture: '' },
          accessToken: 'tok',
          isAuthenticated: true,
          isLoading: false,
        },
      },
    });
    expect(screen.getByText('Logout')).toBeInTheDocument();
  });

  it('dispatches toggleTheme when theme button is clicked', () => {
    const { store } = renderWithProviders(<NavBar />);
    const before = store.getState().ui.themeMode;
    // The theme toggle button is the first IconButton after the menu icon
    const themeBtn = screen.getAllByRole('button')[1]; // [0]=menu, [1]=theme
    fireEvent.click(themeBtn);
    const after = store.getState().ui.themeMode;
    expect(after).not.toBe(before);
  });

  it('dispatches setDrawerOpen when menu icon is clicked', () => {
    const { store } = renderWithProviders(<NavBar />);
    expect(store.getState().ui.drawerOpen).toBe(false);
    const menuBtn = screen.getAllByRole('button')[0];
    fireEvent.click(menuBtn);
    expect(store.getState().ui.drawerOpen).toBe(true);
  });
});
