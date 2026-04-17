import { Middleware } from '@reduxjs/toolkit';
import { api } from '../api/baseApi';
import { updateJobStatus, showSnackbar } from './slices/uiSlice';

export const wsConnect = () => ({ type: 'ws/connect' as const });
export const wsDisconnect = () => ({ type: 'ws/disconnect' as const });

const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000/ws';
const MAX_RECONNECT_ATTEMPTS = 20;

export const wsMiddleware: Middleware = (store) => {
  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let intentionalDisconnect = false;

  const scheduleReconnect = () => {
    if (intentionalDisconnect) return;
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      store.dispatch(showSnackbar({ message: 'Connection lost', severity: 'error' }));
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
    reconnectAttempts++;
    reconnectTimer = setTimeout(() => {
      store.dispatch(wsConnect());
    }, delay);
  };

  const connect = () => {
    const token = (store.getState() as { auth: { accessToken: string | null } }).auth.accessToken;
    if (!token) return;

    ws = new WebSocket(`${WS_BASE_URL}?token=${encodeURIComponent(token)}`);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string) as {
          type: string;
          collection_id?: string;
          job_id?: string;
          status?: string;
          progress?: number;
        };
        switch (msg.type) {
          case 'graph_update':
            store.dispatch(
              api.util.invalidateTags([
                { type: 'Graph' as const },
                { type: 'Node' as const },
                { type: 'GraphNode' as const, id: msg.collection_id },
              ])
            );
            break;
          case 'progress':
          case 'job_progress':
            if (msg.job_id) {
              store.dispatch(updateJobStatus({
                jobId: msg.job_id,
                status: msg.status,
                progress: msg.progress,
              }));
            }
            break;
          case 'job_completed':
            store.dispatch(api.util.invalidateTags(['Collection', 'Document', 'IngestJob']));
            break;
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onopen = () => {
      reconnectAttempts = 0;
    };

    ws.onerror = () => {
      ws = null;
      scheduleReconnect();
    };

    ws.onclose = () => {
      ws = null;
      scheduleReconnect();
    };
  };

  return (next) => (action) => {
    const typedAction = action as { type: string };

    if (typedAction.type === 'ws/connect') {
      if (ws) ws.close();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      intentionalDisconnect = false;
      connect();
    }

    if (typedAction.type === 'ws/disconnect') {
      intentionalDisconnect = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      ws?.close();
      ws = null;
    }

    return next(action);
  };
};
