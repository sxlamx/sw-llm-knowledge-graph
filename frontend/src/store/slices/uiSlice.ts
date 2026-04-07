import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface UiState {
  drawerOpen: boolean;
  sidebarOpen: boolean;
  themeMode: 'light' | 'dark';
  snackbar: { open: boolean; message: string; severity: 'success' | 'error' | 'info' | 'warning' };
  jobStatuses: Record<string, { status: string; progress: number }>;
}

const initialState: UiState = {
  drawerOpen: false,
  sidebarOpen: true,
  themeMode: 'light',
  snackbar: { open: false, message: '', severity: 'info' },
  jobStatuses: {},
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
    updateJobStatus: (state, action: PayloadAction<{ jobId: string; status?: string; progress?: number }>) => {
      if (!state.jobStatuses[action.payload.jobId]) {
        state.jobStatuses[action.payload.jobId] = { status: 'unknown', progress: 0 };
      }
      if (action.payload.status !== undefined) {
        state.jobStatuses[action.payload.jobId].status = action.payload.status;
      }
      if (action.payload.progress !== undefined) {
        state.jobStatuses[action.payload.jobId].progress = action.payload.progress;
      }
    },
  },
});

export const { setDrawerOpen, setSidebarOpen, toggleTheme, showSnackbar, closeSnackbar, updateJobStatus } =
  uiSlice.actions;
export default uiSlice.reducer;
