import { api } from './baseApi';

export type AgentEventType = 'start' | 'thought' | 'observation' | 'token' | 'answer' | 'error';

export interface AgentEvent {
  type: AgentEventType;
  hop?: number;
  content?: string;
  query?: string;
  collection_id?: string;
  hops_taken?: number;
  nodes_visited?: string[];
}

export interface AgentStatusResponse {
  collection_id: string;
  ready: boolean;
  node_count: number;
  edge_count: number;
  max_hops: number;
}

export const agentApi = api.injectEndpoints({
  endpoints: (builder) => ({
    getAgentStatus: builder.query<AgentStatusResponse, { collection_id: string }>({
      query: ({ collection_id }) => `/agent/status?collection_id=${collection_id}`,
    }),
  }),
});

export const { useGetAgentStatusQuery } = agentApi;

// ---------------------------------------------------------------------------
// SSE streaming helper (POST returns text/event-stream)
// ---------------------------------------------------------------------------

/**
 * Stream agent events from POST /api/v1/agent/query.
 * Calls `onEvent` for each parsed SSE data line, then `onDone` when [DONE].
 * Returns an AbortController so the caller can cancel the stream.
 */
export function streamAgentQuery(
  baseUrl: string,
  token: string,
  collectionId: string,
  query: string,
  maxHops: number,
  onEvent: (event: AgentEvent) => void,
  onDone: () => void,
  onError: (err: string) => void,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(`${baseUrl}/api/v1/agent/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ collection_id: collectionId, query, max_hops: maxHops }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        onError(`Server error: ${resp.status}`);
        onDone();
        return;
      }

      const reader = resp.body?.getReader();
      if (!reader) { onDone(); return; }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') { onDone(); return; }
            try {
              onEvent(JSON.parse(data) as AgentEvent);
            } catch {
              // ignore malformed lines
            }
          }
        }
      }
    } catch (err: unknown) {
      if ((err as Error)?.name !== 'AbortError') {
        onError((err as Error)?.message ?? 'Unknown error');
      }
    }
    onDone();
  })();

  return controller;
}
