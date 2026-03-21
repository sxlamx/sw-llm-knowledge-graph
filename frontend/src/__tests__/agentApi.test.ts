import { describe, it, expect, vi, beforeEach } from 'vitest';
import { streamAgentQuery, type AgentEvent } from '../api/agentApi';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStream(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line));
      }
      controller.close();
    },
  });
}

function buildFetchMock(lines: string[], status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    body: makeStream(lines),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('streamAgentQuery', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls onEvent for each SSE data line and onDone on [DONE]', async () => {
    const events: AgentEvent[] = [];
    const onDone = vi.fn();
    const onError = vi.fn();

    const lines = [
      'data: {"type":"start","query":"test"}\n',
      'data: {"type":"token","content":"Hello"}\n',
      'data: [DONE]\n',
    ];
    vi.stubGlobal('fetch', buildFetchMock(lines));

    streamAgentQuery('', 'tok', 'col1', 'test', 2, (e) => events.push(e), onDone, onError);

    // wait for async IIFE
    await new Promise((r) => setTimeout(r, 20));

    expect(events).toHaveLength(2);
    expect(events[0].type).toBe('start');
    expect(events[1].type).toBe('token');
    expect(events[1].content).toBe('Hello');
    expect(onDone).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('calls onError and onDone on non-ok response', async () => {
    const onDone = vi.fn();
    const onError = vi.fn();

    vi.stubGlobal('fetch', buildFetchMock([], 403));

    streamAgentQuery('', 'tok', 'col1', 'q', 2, vi.fn(), onDone, onError);
    await new Promise((r) => setTimeout(r, 20));

    expect(onError).toHaveBeenCalledWith('Server error: 403');
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('returns AbortController and stops on abort', async () => {
    const onError = vi.fn();
    const onDone = vi.fn();

    // Fetch that never resolves (simulates a hanging stream)
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_url: string, opts: RequestInit) => {
      return new Promise((_resolve, _reject) => {
        opts.signal?.addEventListener('abort', () => {
          _reject(Object.assign(new Error('AbortError'), { name: 'AbortError' }));
        });
      });
    }));

    const ctrl = streamAgentQuery('', 'tok', 'col1', 'q', 2, vi.fn(), onDone, onError);
    ctrl.abort();
    await new Promise((r) => setTimeout(r, 20));

    // AbortError should NOT propagate to onError
    expect(onError).not.toHaveBeenCalled();
  });

  it('ignores malformed JSON data lines', async () => {
    const events: AgentEvent[] = [];
    const onDone = vi.fn();

    const lines = [
      'data: not-json\n',
      'data: {"type":"answer","content":"ok"}\n',
      'data: [DONE]\n',
    ];
    vi.stubGlobal('fetch', buildFetchMock(lines));

    streamAgentQuery('', 'tok', 'col1', 'q', 2, (e) => events.push(e), onDone, vi.fn());
    await new Promise((r) => setTimeout(r, 20));

    // malformed line is skipped; only valid event received
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('answer');
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('skips non-data SSE lines (comment, empty)', async () => {
    const events: AgentEvent[] = [];

    const lines = [
      ': heartbeat\n',
      '\n',
      'data: {"type":"thought","content":"thinking"}\n',
      'data: [DONE]\n',
    ];
    vi.stubGlobal('fetch', buildFetchMock(lines));

    streamAgentQuery('', 'tok', 'col1', 'q', 2, (e) => events.push(e), vi.fn(), vi.fn());
    await new Promise((r) => setTimeout(r, 20));

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('thought');
  });

  it('sends correct Authorization header and POST body', async () => {
    const fetchMock = buildFetchMock(['data: [DONE]\n']);
    vi.stubGlobal('fetch', fetchMock);

    streamAgentQuery('http://api', 'mytoken', 'coll', 'what?', 4, vi.fn(), vi.fn(), vi.fn());
    await new Promise((r) => setTimeout(r, 20));

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/api/v1/agent/query');
    expect(opts.method).toBe('POST');
    expect(opts.headers['Authorization']).toBe('Bearer mytoken');

    const body = JSON.parse(opts.body);
    expect(body.collection_id).toBe('coll');
    expect(body.query).toBe('what?');
    expect(body.max_hops).toBe(4);
  });

  it('calls onDone even when stream ends without [DONE] sentinel', async () => {
    const onDone = vi.fn();
    // Stream closes without [DONE]
    const lines = ['data: {"type":"token","content":"x"}\n'];
    vi.stubGlobal('fetch', buildFetchMock(lines));

    streamAgentQuery('', 'tok', 'col1', 'q', 2, vi.fn(), onDone, vi.fn());
    await new Promise((r) => setTimeout(r, 20));

    expect(onDone).toHaveBeenCalledOnce();
  });
});
