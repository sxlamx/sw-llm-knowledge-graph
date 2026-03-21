import { configureStore } from '@reduxjs/toolkit';
import { TypedUseSelectorHook, useDispatch, useSelector } from 'react-redux';
import { api } from '../api/baseApi';
import authReducer from './slices/authSlice';
import collectionsReducer from './slices/collectionsSlice';
import searchReducer from './slices/searchSlice';
import graphReducer from './slices/graphSlice';
import uiReducer from './slices/uiSlice';
import { wsMiddleware } from './wsMiddleware';

export const store = configureStore({
  reducer: {
    auth: authReducer,
    collections: collectionsReducer,
    search: searchReducer,
    graph: graphReducer,
    ui: uiReducer,
    [api.reducerPath]: api.reducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware()
      .concat(api.middleware)
      .concat(wsMiddleware),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;
