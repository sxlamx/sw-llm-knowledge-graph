/**
 * Tests for the useCollabRoom WebSocket hook.
 * We simulate WebSocket messages by capturing the ws.onmessage handler.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import { Provider } from 'react-redux';
import { makeStore } from './test-utils';
import { useCollabRoom } from '../hooks/useCollabRoom';

// ---------------------------------------------------------------------------
// WebSocket stub
// ---------------------------------------------------------------------------

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = WebSocket.OPEN;
  url: string;
  send = vi.fn();
  close = vi.fn().mockImplementation(() => {
    this.readyState = WebSocket.CLOSED;
    this.onclose?.();
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  /** Test helper — simulate a server message */
  receive(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function wrapper(store: ReturnType<typeof makeStore>) {
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(Provider, { store }, children);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useCollabRoom', () => {
  it('does not open a socket when collectionId is undefined', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    renderHook(() => useCollabRoom(undefined), { wrapper: wrapper(store) });
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('does not open a socket when token is null', () => {
    const store = makeStore({
      auth: { user: null, accessToken: null, isAuthenticated: false, isLoading: false },
    });
    renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('opens a socket with the correct URL', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'mytoken', isAuthenticated: true, isLoading: false },
    });
    renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain('collab/col-1');
    expect(MockWebSocket.instances[0].url).toContain('token=mytoken');
  });

  it('dispatches setPresence on "presence" viewing message', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.receive({
        type: 'presence',
        action: 'viewing',
        user_id: 'u2',
        name: 'Bob',
        node_id: 'n42',
      });
    });

    const presence = store.getState().graph.presence;
    expect(presence['u2']).toEqual({ user_id: 'u2', name: 'Bob', node_id: 'n42' });
  });

  it('dispatches removePresence on "presence" leave message', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
      graph: {
        presence: { u2: { user_id: 'u2', name: 'Bob', node_id: 'n42' } },
        selectedNodeId: null, pathFinderMode: false, pathEndpoints: [null, null],
        depth: 2, edgeTypeFilters: [], topicFilters: [],
      },
    });
    renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.receive({ type: 'presence', action: 'leave', user_id: 'u2' });
    });

    expect(store.getState().graph.presence['u2']).toBeUndefined();
  });

  it('sendPresence sends a viewing op', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    const { result } = renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    act(() => {
      result.current.sendPresence('n99');
    });

    expect(ws.send).toHaveBeenCalledOnce();
    const msg = JSON.parse(ws.send.mock.calls[0][0] as string);
    expect(msg.op).toBe('presence');
    expect(msg.node_id).toBe('n99');
    expect(msg.action).toBe('viewing');
  });

  it('sendNodeUpdate sends a node_update op', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    const { result } = renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    act(() => {
      result.current.sendNodeUpdate('n1', { label: 'Company' });
    });

    const msg = JSON.parse(ws.send.mock.calls[0][0] as string);
    expect(msg.op).toBe('node_update');
    expect(msg.node_id).toBe('n1');
    expect(msg.patch).toEqual({ label: 'Company' });
  });

  it('clears presence and closes socket on unmount', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    const { unmount } = renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    // Seed some presence
    act(() => {
      ws.receive({ type: 'presence', action: 'viewing', user_id: 'u3', name: 'Carol', node_id: 'n1' });
    });
    expect(Object.keys(store.getState().graph.presence)).toHaveLength(1);

    unmount();

    expect(ws.close).toHaveBeenCalledOnce();
    expect(store.getState().graph.presence).toEqual({});
  });

  it('ignores malformed JSON messages', () => {
    const store = makeStore({
      auth: { user: null, accessToken: 'tok', isAuthenticated: true, isLoading: false },
    });
    renderHook(() => useCollabRoom('col-1'), { wrapper: wrapper(store) });
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.onmessage?.({ data: 'not-json{{' } as MessageEvent);
    });

    // Should not throw; presence stays empty
    expect(store.getState().graph.presence).toEqual({});
  });
});
