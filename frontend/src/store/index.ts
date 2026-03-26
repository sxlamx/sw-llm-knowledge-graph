import { configureStore } from '@reduxjs/toolkit';
import { useDispatch, useSelector } from 'react-redux';
import authReducer from './slices/authSlice';
import uiReducer from './slices/uiSlice';
import graphReducer from './slices/graphSlice';
import collectionsReducer from './slices/collectionsSlice';
import searchReducer from './slices/searchSlice';
import { api } from '../api/baseApi';

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
    getDefaultMiddleware().concat(api.middleware),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector = <T>(selector: (state: RootState) => T) =>
  useSelector(selector);
