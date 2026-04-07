import { Middleware } from '@reduxjs/toolkit';
import { api } from '../api/baseApi';
import { updateJobStatus } from './slices/uiSlice';

export const wsConnect = () => ({ type: 'ws/connect' as const });
export const wsDisconnect = () => ({ type: 'ws/disconnect' as const });

const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000/ws';

export const wsMiddleware: Middleware = (store) => {
  let ws: WebSocket | null = null;

  return (next) => (action) => {
    const typedAction = action as { type: string };

    if (typedAction.type === 'ws/connect') {
      if (ws) ws.close();
      const token = (store.getState() as { auth: { accessToken: string | null } }).auth.accessToken;
      if (!token) return next(action);

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

      ws.onerror = () => {
        ws = null;
      };

      ws.onclose = () => {
        ws = null;
      };
    }

    if (typedAction.type === 'ws/disconnect') {
      ws?.close();
      ws = null;
    }

    return next(action);
  };
};
