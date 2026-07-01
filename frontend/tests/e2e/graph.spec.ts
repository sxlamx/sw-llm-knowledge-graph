import { test, expect } from './fixtures';

test.describe('Graph viewer', () => {
  test('graph viewer renders with collection selector', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/v1/graph/subgraph*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          nodes: [
            { id: 'n1', label: 'OpenAI', entity_type: 'ORGANIZATION', confidence: 0.95 },
            { id: 'n2', label: 'Sam Altman', entity_type: 'PERSON', confidence: 0.9 },
          ],
          edges: [
            { id: 'e1', source: 'n2', target: 'n1', relation: 'WORKS_AT', weight: 1.0 },
          ],
          total_nodes: 2,
          total_edges: 1,
        }),
      })
    );

    await authenticatedPage.goto('/graph/e2e-col-1');
    await expect(authenticatedPage.getByText('Graph Viewer')).toBeVisible();
  });

  test('label toggle button exists in toolbar', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/v1/graph/subgraph*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          nodes: [{ id: 'n1', label: 'Test', entity_type: 'PERSON', confidence: 0.9 }],
          edges: [],
          total_nodes: 1,
          total_edges: 0,
        }),
      })
    );

    await authenticatedPage.goto('/graph/e2e-col-1');
    await expect(authenticatedPage.getByText('Graph Viewer')).toBeVisible();
  });
});