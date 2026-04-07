import { configureStore } from '@reduxjs/toolkit';
import { useDispatch, useSelector } from 'react-redux';
import authReducer from './slices/authSlice';
import uiReducer from './slices/uiSlice';
import graphReducer from './slices/graphSlice';
import collectionsReducer from './slices/collectionsSlice';
import searchReducer from './slices/searchSlice';
import { api } from '../api/baseApi';
import { wsMiddleware } from './wsMiddleware';

export const store = configureStore({
  reducer: {
    auth: authReducer,
    ui: uiReducer,
    graph: graphReducer,
    collections: collectionsReducer,
    search: searchReducer,
    [api.reducerPath]: api.reducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().concat(api.middleware).concat(wsMiddleware),
});

export type AppDispatch = typeof store.dispatch;
type AuthState = ReturnType<typeof authReducer>;
type UiState = ReturnType<typeof uiReducer>;
type GraphState = ReturnType<typeof graphReducer>;
type CollectionsState = ReturnType<typeof collectionsReducer>;
type SearchState = ReturnType<typeof searchReducer>;
type ApiState = ReturnType<typeof api.reducer>;
export type RootState = {
  auth: AuthState;
  ui: UiState;
  graph: GraphState;
  collections: CollectionsState;
  search: SearchState;
  api: ApiState;
};

export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector = <T>(selector: (state: RootState) => T) =>
  useSelector(selector);
