import { createSlice, PayloadAction } from '@reduxjs/toolkit';
import type { User } from '../types/api';

interface AuthState {
  token: string | null;
  user: User | null;
}

const initialState: AuthState = {
  token: sessionStorage.getItem('access_token'),
  user: (() => {
    try {
      const u = sessionStorage.getItem('user');
      return u ? JSON.parse(u) : null;
    } catch {
      return null;
    }
  })(),
};

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    setCredentials(state, action: PayloadAction<{ token: string; user: User }>) {
      state.token = action.payload.token;
      state.user = action.payload.user;
      sessionStorage.setItem('access_token', action.payload.token);
      sessionStorage.setItem('user', JSON.stringify(action.payload.user));
    },
    clearCredentials(state) {
      state.token = null;
      state.user = null;
      sessionStorage.removeItem('access_token');
      sessionStorage.removeItem('user');
    },
  },
});

export const { setCredentials, clearCredentials } = authSlice.actions;
export default authSlice.reducer;
