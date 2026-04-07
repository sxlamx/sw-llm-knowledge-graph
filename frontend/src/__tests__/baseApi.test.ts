/**
 * Unit tests for baseApi reauth flow.
 *
 * Tests the reauth logic by verifying the correct actions are exported.
 */
import { describe, it, expect } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import { api } from '../api/baseApi';
import authReducer, { setCredentials, setAccessToken, logout } from '../store/slices/authSlice';
import uiReducer from '../store/slices/uiSlice';
import graphReducer from '../store/slices/graphSlice';
import collectionsReducer from '../store/slices/collectionsSlice';
import searchReducer from '../store/slices/searchSlice';

describe('baseApi reauth logic', () => {
  it('auth/setAccessToken action is exported and has correct type', () => {
    expect(setAccessToken('token')).toBeDefined();
    expect(setAccessToken('token').type).toBe('auth/setAccessToken');
    expect(setAccessToken('token').payload).toBe('token');
  });

  it('auth/setCredentials action is exported and has correct payload', () => {
    const user = { id: 'u1', email: 'a@b.com', name: 'Test' };
    const action = setCredentials({ accessToken: 'tok', user });
    expect(action).toBeDefined();
    expect(action.type).toBe('auth/setCredentials');
    expect(action.payload.accessToken).toBe('tok');
    expect(action.payload.user).toEqual(user);
  });

  it('auth/logout action is exported', () => {
    expect(logout()).toBeDefined();
    expect(logout().type).toBe('auth/logout');
  });

  it('baseApi has correct reducerPath', () => {
    expect(api.reducerPath).toBe('api');
  });

  it('configureStore can create store with api middleware', () => {
    const store = configureStore({
      reducer: {
        auth: authReducer,
        ui: uiReducer,
        graph: graphReducer,
        collections: collectionsReducer,
        search: searchReducer,
        [api.reducerPath]: api.reducer,
      },
      middleware: (getDefault) => getDefault().concat(api.middleware),
    });
    expect(store.getState()).toBeDefined();
  });
});
