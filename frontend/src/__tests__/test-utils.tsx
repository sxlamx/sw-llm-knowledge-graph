/**
 * Shared test utilities — renders with a Redux store + Router context.
 */
import React from 'react';
import { render, type RenderOptions } from '@testing-library/react';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../api/baseApi';
import authReducer from '../store/slices/authSlice';
import collectionsReducer from '../store/slices/collectionsSlice';
import searchReducer from '../store/slices/searchSlice';
import graphReducer from '../store/slices/graphSlice';
import uiReducer from '../store/slices/uiSlice';

export function makeStore(preloadedState?: Record<string, unknown>) {
  return configureStore({
    reducer: {
      auth: authReducer,
      collections: collectionsReducer,
      search: searchReducer,
      graph: graphReducer,
      ui: uiReducer,
      [api.reducerPath]: api.reducer,
    },
    middleware: (g) => g({ serializableCheck: false }).concat(api.middleware),
    preloadedState: preloadedState as never,
  });
}

interface WrapperOptions extends RenderOptions {
  preloadedState?: Record<string, unknown>;
  initialEntries?: string[];
}

export function renderWithProviders(
  ui: React.ReactElement,
  { preloadedState, initialEntries = ['/'], ...opts }: WrapperOptions = {},
) {
  const store = makeStore(preloadedState);
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <Provider store={store}>
        <MemoryRouter
          initialEntries={initialEntries}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {children}
        </MemoryRouter>
      </Provider>
    );
  }
  return { store, ...render(ui, { wrapper: Wrapper, ...opts }) };
}
