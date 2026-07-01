import { test, expect } from './fixtures';

test.describe('Ingest flow', () => {
  test('collection page shows ingest panel and document table', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/v1/collections/e2e-col-1', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'e2e-col-1', name: 'E2E Collection', doc_count: 2, status: 'active' }),
      })
    );

    await authenticatedPage.route('**/api/v1/documents*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: [
            { id: 'd1', title: 'Report.pdf', file_type: 'pdf', chunk_count: 5, status: 'indexed' },
          ],
          total: 1,
        }),
      })
    );

    await authenticatedPage.goto('/collection/e2e-col-1');
    await expect(authenticatedPage.getByText('E2E Collection')).toBeVisible();
    await expect(authenticatedPage.getByText('Ingest Documents')).toBeVisible();
    await expect(authenticatedPage.getByText('Report.pdf')).toBeVisible();
  });
});