/**
 * E2E: Collaborative graph editing
 *
 * Verifies that the graph viewer joins the collab WebSocket room
 * and shows presence indicators when another user connects.
 *
 * Uses Playwright's built-in WebSocket route interception to inject
 * server messages without needing a real backend.
 */
import { test, expect } from './fixtures';

const COLLECTION_ID = 'e2e-col-collab';

const MOCK_GRAPH = {
  nodes: [
    { id: 'n1', label: 'Alice', entity_type: 'Person', description: null,
      confidence: 0.9, properties: {}, source_chunk_ids: [], topics: [],
      collection_id: COLLECTION_ID },
  ],
  edges: [],
  total_nodes: 1,
  total_edges: 0,
};

test.describe('Collaborative Editing', () => {
  test.beforeEach(async ({ authedPage: page }) => {
    await page.route('**/api/v1/graph/subgraph*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_GRAPH) })
    );
    await page.route('**/api/v1/analytics/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) })
    );
    await page.route('**/api/v1/collections', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ collections: [{ id: COLLECTION_ID, name: 'Collab Test', doc_count: 1 }], total: 1 }),
      })
    );
  });

  test('graph viewer connects to collab WebSocket on mount', async ({ authedPage: page }) => {
    const wsPromise = page.waitForEvent('websocket', (ws) => ws.url().includes('/ws/collab/'));

    await page.goto(`/graph/${COLLECTION_ID}`);

    const ws = await wsPromise;
    expect(ws.url()).toContain(`/ws/collab/${COLLECTION_ID}`);
    expect(ws.url()).toContain('token=');
  });

  test('presence avatars appear when another user joins', async ({ authedPage: page }) => {
    let sendFromServer: ((data: string) => void) | null = null;

    await page.routeWebSocket('**/ws/collab/**', (ws) => {
      sendFromServer = (data) => ws.send(data);
      // Intercept and accept connection
      ws.onMessage(() => { /* ignore client messages */ });
    });

    await page.goto(`/graph/${COLLECTION_ID}`);

    // Simulate the server pushing a "join" presence event
    await page.waitForTimeout(500); // let the WS connect
    if (sendFromServer) {
      sendFromServer(JSON.stringify({
        type: 'presence',
        action: 'viewing',
        user_id: 'bob-id',
        name: 'Bob',
        node_id: 'n1',
        ts: Date.now(),
      }));
    }

    // Presence avatars should appear in the toolbar (AvatarGroup)
    await expect(page.getByText('B')).toBeVisible({ timeout: 5_000 });
  });

  test('presence disappears when user leaves', async ({ authedPage: page }) => {
    let sendFromServer: ((data: string) => void) | null = null;

    await page.routeWebSocket('**/ws/collab/**', (ws) => {
      sendFromServer = (data) => ws.send(data);
      ws.onMessage(() => {});
    });

    await page.goto(`/graph/${COLLECTION_ID}`);
    await page.waitForTimeout(500);

    if (sendFromServer) {
      // Join
      sendFromServer(JSON.stringify({ type: 'presence', action: 'viewing', user_id: 'carol', name: 'Carol', node_id: 'n1', ts: Date.now() }));
      await page.waitForTimeout(200);
      // Leave
      sendFromServer(JSON.stringify({ type: 'presence', action: 'leave', user_id: 'carol', ts: Date.now() }));
    }

    // After leave event, the avatar should be gone
    await page.waitForTimeout(300);
    await expect(page.getByText('C')).not.toBeVisible();
  });
});
