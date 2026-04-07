/**
 * Unit tests for RequireAuth component.
 *
 * Verifies:
 * - Authenticated user can access protected routes
 * - Unauthenticated user is redirected to login
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import authReducer from '../store/slices/authSlice';
import RequireAuth from '../components/auth/RequireAuth';

function renderWithRouter(store: ReturnType<typeof configureStore>, initialEntry: string) {
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[initialEntry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<div>Login Page</div>} />
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <div>Dashboard Content</div>
              </RequireAuth>
            }
          />
          <Route
            path="/collection/:id"
            element={
              <RequireAuth>
                <div>Collection Page</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>
    </Provider>
  );
}

describe('RequireAuth', () => {
  it('redirects unauthenticated user to login page (/)', () => {
    const store = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: { user: null, accessToken: null, isAuthenticated: false, isLoading: false },
      },
    });

    renderWithRouter(store, '/dashboard');

    expect(screen.getByText('Login Page')).toBeInTheDocument();
    expect(screen.queryByText('Dashboard Content')).not.toBeInTheDocument();
  });

  it('allows authenticated user to access protected route', () => {
    const store = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: {
          user: { id: 'u1', email: 'test@example.com', name: 'Test User' },
          accessToken: 'valid-token',
          isAuthenticated: true,
          isLoading: false,
        },
      },
    });

    renderWithRouter(store, '/dashboard');

    expect(screen.getByText('Dashboard Content')).toBeInTheDocument();
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument();
  });

  it('redirects unauthenticated user from /collection/:id', () => {
    const store = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: { user: null, accessToken: null, isAuthenticated: false, isLoading: false },
      },
    });

    renderWithRouter(store, '/collection/col-123');

    expect(screen.getByText('Login Page')).toBeInTheDocument();
    expect(screen.queryByText('Collection Page')).not.toBeInTheDocument();
  });

  it('isAuthenticated=true but user=null still grants access (token restored from localStorage)', () => {
    const store = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: {
          user: null,
          accessToken: 'restored-token',
          isAuthenticated: true,
          isLoading: false,
        },
      },
    });

    renderWithRouter(store, '/dashboard');

    expect(screen.getByText('Dashboard Content')).toBeInTheDocument();
  });

  it('redirects when isAuthenticated=false regardless of token presence', () => {
    const store = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: {
          user: null,
          accessToken: 'some-token',
          isAuthenticated: false,
          isLoading: false,
        },
      },
    });

    renderWithRouter(store, '/dashboard');

    expect(screen.getByText('Login Page')).toBeInTheDocument();
  });
});
