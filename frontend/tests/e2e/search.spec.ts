import { test, expect } from './fixtures';

test.describe('Search', () => {
  test('search page renders all 4 mode toggles', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/search');

    await expect(authenticatedPage.getByText('Search')).toBeVisible();
    await expect(authenticatedPage.getByRole('button', { name: 'Hybrid' })).toBeVisible();
    await expect(authenticatedPage.getByRole('button', { name: 'Vector' })).toBeVisible();
    await expect(authenticatedPage.getByRole('button', { name: 'BM25' })).toBeVisible();
    await expect(authenticatedPage.getByRole('button', { name: 'Graph' })).toBeVisible();
  });

  test('search returns results', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/v1/search', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [{ id: 'r1', chunk_id: 'c1', doc_title: 'Test Doc', text: 'Test result', score: 0.9, page: 1 }],
          total: 1,
          latency_ms: 50,
          query: 'test',
        }),
      })
    );

    await authenticatedPage.goto('/search');
    const input = authenticatedPage.getByLabelText(/search knowledge graph/i);
    await input.fill('test query');
    await input.press('Enter');

    await expect(authenticatedPage.getByText('Test Doc')).toBeVisible({ timeout: 5000 });
  });
});