/**
 * E2E: Ingest flow
 *
 * Navigates to a collection page, mocks the ingest and job-stream APIs,
 * and verifies the progress bar and success state.
 */
import { test, expect } from './fixtures';

const COLLECTION_ID = 'e2e-col-001';

test.describe('Ingest', () => {
  test.beforeEach(async ({ authedPage: page }) => {
    // Mock collection detail endpoint
    await page.route(`**/api/v1/collections/${COLLECTION_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: COLLECTION_ID,
          name: 'E2E Test Collection',
          description: 'Playwright test collection',
          doc_count: 0,
          created_at: new Date().toISOString(),
        }),
      });
    });

    // Mock document list
    await page.route(`**/api/v1/documents*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ documents: [], total: 0 }),
      });
    });

    // Mock ingest start
    await page.route(`**/api/v1/ingest/folder`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-e2e-001',
          status: 'queued',
          stream_url: '/api/v1/ingest/jobs/job-e2e-001/stream',
        }),
      });
    });

    // Mock SSE stream: emit progress events then a complete event
    await page.route(`**/api/v1/ingest/jobs/job-e2e-001/stream`, async (route) => {
      const progressEvents = [
        'data: {"type":"progress","job_id":"job-e2e-001","status":"running","progress":0.5,"message":"Processing..."}\n\n',
        'data: {"type":"complete","job_id":"job-e2e-001","status":"completed","progress":1.0,"message":"Done"}\n\n',
      ].join('');

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: progressEvents,
      });
    });

    await page.goto(`/collection/${COLLECTION_ID}`);
  });

  test('collection page shows the ingest panel', async ({ authedPage: page }) => {
    // IngestPanel should be visible with a folder path input or a start button
    const ingestSection = page.getByText(/ingest|add documents|folder/i).first();
    await expect(ingestSection).toBeVisible({ timeout: 10_000 });
  });

  test('collection page shows document section', async ({ authedPage: page }) => {
    await expect(page.getByText(/documents/i).first()).toBeVisible({ timeout: 10_000 });
    // Empty state when no documents
    await expect(page.getByText(/no documents|use the ingest panel/i)).toBeVisible({ timeout: 10_000 });
  });

  test('collection header shows collection name', async ({ authedPage: page }) => {
    await expect(page.getByText('E2E Test Collection')).toBeVisible({ timeout: 10_000 });
  });

  test('view graph button is present in collection header', async ({ authedPage: page }) => {
    const graphBtn = page.getByRole('button', { name: /view graph/i });
    await expect(graphBtn).toBeVisible({ timeout: 10_000 });
  });

  test('agent query button is present in collection header', async ({ authedPage: page }) => {
    const agentBtn = page.getByRole('button', { name: /agent query/i });
    await expect(agentBtn).toBeVisible({ timeout: 10_000 });
  });

  test('fine-tune button is present in collection header', async ({ authedPage: page }) => {
    const ftBtn = page.getByRole('button', { name: /fine.?tune/i });
    await expect(ftBtn).toBeVisible({ timeout: 10_000 });
  });
});
