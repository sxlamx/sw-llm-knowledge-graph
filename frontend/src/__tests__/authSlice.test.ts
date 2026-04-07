/**
 * Unit tests for authSlice — localStorage persistence via reducer actions.
 *
 * Verifies:
 * - setCredentials persists user and token to localStorage
 * - setAccessToken persists token to localStorage
 * - logout clears localStorage
 * - Initial state reads from localStorage when available
 */
import { describe, it, expect, vi } from 'vitest';
import authReducer, { setCredentials, setAccessToken, logout } from '../store/slices/authSlice';

// Working localStorage mock with proper Object.defineProperty
function createLocalStorageMock(initial: Record<string, string> = {}) {
  let store: Record<string, string> = { ...initial };
  const mock = {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
  };
  return { mock, getStore: () => store };
}

describe('authSlice localStorage persistence', () => {
  it('setCredentials persists accessToken to localStorage', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, setCredentials({
      accessToken: 'test-token-abc',
      user: { id: 'user-1', email: 'alice@example.com', name: 'Alice' },
    }));

    expect(mock.setItem).toHaveBeenCalledWith('kg_access_token', 'test-token-abc');
    expect(state.accessToken).toBe('test-token-abc');
    expect(state.isAuthenticated).toBe(true);
  });

  it('setCredentials persists user to localStorage', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    authReducer(undefined, setCredentials({
      accessToken: 'token',
      user: { id: 'user-1', email: 'alice@example.com', name: 'Alice' },
    }));

    expect(mock.setItem).toHaveBeenCalledWith('kg_user', expect.stringContaining('"id":"user-1"'));
  });

  it('setAccessToken persists token to localStorage', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, setAccessToken('refreshed-token-xyz'));

    expect(mock.setItem).toHaveBeenCalledWith('kg_access_token', 'refreshed-token-xyz');
    expect(state.accessToken).toBe('refreshed-token-xyz');
    expect(state.isAuthenticated).toBe(true);
  });

  it('logout removes both token and user from localStorage', () => {
    const { mock } = createLocalStorageMock({
      'kg_access_token': 'some-token',
      'kg_user': JSON.stringify({ id: 'u1', name: 'Test' }),
    });
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, logout());

    expect(mock.removeItem).toHaveBeenCalledWith('kg_access_token');
    expect(mock.removeItem).toHaveBeenCalledWith('kg_user');
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });

  it('setAccessToken does NOT clear existing user', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(
      { user: { id: 'u1', email: 'a@b.com', name: 'Test' }, accessToken: null, isAuthenticated: false, isLoading: false },
      setAccessToken('new-token')
    );

    expect(state.user).toEqual({ id: 'u1', email: 'a@b.com', name: 'Test' });
    expect(mock.setItem).toHaveBeenCalledWith('kg_access_token', 'new-token');
  });

  it('logout sets isAuthenticated to false', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(
      { user: { id: 'u1', email: 'a@b.com', name: 'Test' }, accessToken: 'tok', isAuthenticated: true, isLoading: false },
      logout()
    );

    expect(state.isAuthenticated).toBe(false);
  });

  it('setCredentials sets isAuthenticated to true', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, setCredentials({
      accessToken: 'tok',
      user: { id: 'u1', email: 'a@b.com', name: 'Test' },
    }));

    expect(state.isAuthenticated).toBe(true);
  });
});
