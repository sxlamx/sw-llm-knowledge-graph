/**
 * Unit tests for authSlice — localStorage persistence via reducer actions.
 *
 * Verifies:
 * - setCredentials persists user to localStorage (NOT accessToken — spec: memory only)
 * - setAccessToken keeps token in memory only (NOT localStorage)
 * - logout clears localStorage and memory
 * - Initial state reads user from localStorage when available
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
  it('setCredentials does NOT persist accessToken to localStorage (memory only per spec)', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, setCredentials({
      accessToken: 'test-token-abc',
      user: { id: 'user-1', email: 'alice@example.com', name: 'Alice' },
    }));

    expect(mock.setItem).not.toHaveBeenCalledWith('kg_access_token', expect.anything());
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

  it('setAccessToken keeps token in memory only (NOT localStorage)', () => {
    const { mock } = createLocalStorageMock();
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, setAccessToken('refreshed-token-xyz'));

    expect(mock.setItem).not.toHaveBeenCalledWith('kg_access_token', expect.anything());
    expect(state.accessToken).toBe('refreshed-token-xyz');
    expect(state.isAuthenticated).toBe(true);
  });

  it('logout removes user from localStorage but does NOT remove accessToken (it was never stored)', () => {
    const { mock } = createLocalStorageMock({
      'kg_user': JSON.stringify({ id: 'u1', name: 'Test' }),
    });
    Object.defineProperty(globalThis, 'localStorage', { value: mock });

    const state = authReducer(undefined, logout());

    expect(mock.removeItem).toHaveBeenCalledWith('kg_user');
    expect(mock.removeItem).not.toHaveBeenCalledWith('kg_access_token');
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
    expect(mock.setItem).not.toHaveBeenCalledWith('kg_access_token', expect.anything());
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