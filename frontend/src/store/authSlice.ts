/**
 * Re-exports from the canonical auth slice.
 * Keep all imports pointing here for backward compatibility.
 */
export { default, setCredentials, setAccessToken, setLoading, logout } from './slices/authSlice';
/** Alias used by older components that call clearCredentials() */
export { logout as clearCredentials } from './slices/authSlice';
