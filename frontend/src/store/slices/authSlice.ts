import { createSlice, PayloadAction } from '@reduxjs/toolkit';
import type { User } from '../../types/api';

export type { User };

interface AuthState {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

const initialState: AuthState = {
  user: null,
  accessToken: null,
  isAuthenticated: false,
  isLoading: false,
};

// Restore user + token from localStorage on init
try {
  const storedUser = localStorage.getItem('kg_user');
  const storedToken = localStorage.getItem('kg_access_token');
  if (storedUser) {
    initialState.user = JSON.parse(storedUser);
    if (initialState.user) {
      initialState.isAuthenticated = true;
      if (storedToken) initialState.accessToken = storedToken;
    }
  }
} catch {
  // localStorage not available (e.g. SSR or test environments without jsdom)
}

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    setCredentials: (state, action: PayloadAction<{ user: User; accessToken: string }>) => {
      state.user = action.payload.user;
      state.accessToken = action.payload.accessToken;
      state.isAuthenticated = true;
      state.isLoading = false;
      localStorage.setItem('kg_user', JSON.stringify(action.payload.user));
      localStorage.setItem('kg_access_token', action.payload.accessToken);
    },
    setAccessToken: (state, action: PayloadAction<string>) => {
      state.accessToken = action.payload;
      state.isAuthenticated = true;
      localStorage.setItem('kg_access_token', action.payload);
    },
    setLoading: (state, action: PayloadAction<boolean>) => {
      state.isLoading = action.payload;
    },
    logout: (state) => {
      state.user = null;
      state.accessToken = null;
      state.isAuthenticated = false;
      state.isLoading = false;
      localStorage.removeItem('kg_user');
      localStorage.removeItem('kg_access_token');
    },
  },
});

export const { setCredentials, setAccessToken, setLoading, logout } = authSlice.actions;
export default authSlice.reducer;
