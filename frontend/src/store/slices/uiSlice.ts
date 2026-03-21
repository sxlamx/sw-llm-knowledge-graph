import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface UiState {
  drawerOpen: boolean;
  sidebarOpen: boolean;
  themeMode: 'light' | 'dark';
  snackbar: { open: boolean; message: string; severity: 'success' | 'error' | 'info' | 'warning' };
}

const initialState: UiState = {
  drawerOpen: false,
  sidebarOpen: true,
  themeMode: 'light',
  snackbar: { open: false, message: '', severity: 'info' },
};

const uiSlice = createSlice({
  name: 'ui',
  initialState,
  reducers: {
    setDrawerOpen: (state, action: PayloadAction<boolean>) => {
      state.drawerOpen = action.payload;
    },
    setSidebarOpen: (state, action: PayloadAction<boolean>) => {
      state.sidebarOpen = action.payload;
    },
    toggleTheme: (state) => {
      state.themeMode = state.themeMode === 'light' ? 'dark' : 'light';
    },
    showSnackbar: (
      state,
      action: PayloadAction<{ message: string; severity?: 'success' | 'error' | 'info' | 'warning' }>
    ) => {
      state.snackbar = {
        open: true,
        message: action.payload.message,
        severity: action.payload.severity ?? 'info',
      };
    },
    closeSnackbar: (state) => {
      state.snackbar.open = false;
    },
  },
});

export const { setDrawerOpen, setSidebarOpen, toggleTheme, showSnackbar, closeSnackbar } =
  uiSlice.actions;
export default uiSlice.reducer;
