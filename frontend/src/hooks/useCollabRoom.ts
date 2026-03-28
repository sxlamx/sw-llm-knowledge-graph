/**
 * useCollabRoom — connects to WS /ws/collab/{collectionId} and:
 *   - Dispatches presence events (other users viewing nodes) to Redux
 *   - Dispatches collab graph mutations (node_update, edge_create, edge_delete)
 *     so the local graph cache is invalidated
 *   - Exposes `sendPresence(nodeId)` so the local user can announce which node
 *     they are viewing
 *   - Exposes `sendNodeUpdate(nodeId, patch, ts)` for collaborative edits
 *
 * Returns a ref to the WebSocket and a sendPresence helper.
 */
import { useEffect, useRef, useCallback } from 'react';
import { useAppDispatch, useAppSelector } from '../store';
import { setPresence, removePresence, clearPresence } from '../store/slices/graphSlice';
import { api } from '../api/baseApi';

const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000/ws';

export function useCollabRoom(collectionId: string | undefined) {
  const dispatch = useAppDispatch();
  const token = useAppSelector((s) => s.auth.accessToken);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!collectionId || !token) return;

    let active = true; // guard against StrictMode double-mount cleanup
    const url = `${WS_BASE}/collab/${collectionId}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      if (!active) {
        // StrictMode cleanup already ran — close the now-open socket cleanly
        ws.close();
        return;
      }
      wsRef.current = ws;
    };

    ws.onmessage = (ev) => {
      if (!active) return;
      try {
        const msg = JSON.parse(ev.data as string) as {
          type: string;
          op?: string;
          action?: string;
          user_id?: string;
          name?: string;
          node_id?: string;
        };

        if (msg.type === 'presence') {
          const { action, user_id, name, node_id } = msg;
          if (!user_id) return;
          if (action === 'leave') {
            dispatch(removePresence(user_id));
          } else if (node_id) {
            dispatch(setPresence({ user_id, name: name ?? user_id, node_id }));
          }
        } else if (msg.type === 'collab') {
          // Invalidate graph cache so the viewer re-fetches updated data
          dispatch(api.util.invalidateTags([{ type: 'GraphNode' as const, id: collectionId }]));
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      dispatch(clearPresence());
    };

    return () => {
      active = false;
      // Only close if already open; if still connecting, onopen will close it
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
      wsRef.current = null;
      dispatch(clearPresence());
    };
  }, [collectionId, token, dispatch]);

  const sendPresence = useCallback((nodeId: string | null) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({
      op: 'presence',
      action: nodeId ? 'viewing' : 'leave',
      node_id: nodeId,
      ts: Date.now(),
    }));
  }, []);

  const sendNodeUpdate = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ op: 'node_update', node_id: nodeId, patch, ts: Date.now() }));
  }, []);

  return { sendPresence, sendNodeUpdate };
}
