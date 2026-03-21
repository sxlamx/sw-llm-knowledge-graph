/**
 * E2E: Search page
 *
 * Verifies the search bar is present, a query can be entered, and results
 * (or an empty state) appear.  Uses API mocking to avoid hitting a live backend.
 */
import { test, expect } from './fixtures';

test.describe('Search', () => {
  test.beforeEach(async ({ authedPage: page }) => {
    // Mock the search endpoint so tests are hermetic
    await page.route('**/api/v1/search', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [
            {
              chunk_id: 'chunk-1',
              doc_id: 'doc-1',
              doc_title: 'Sample PDF',
              text: 'This is a sample search result for the E2E test.',
              highlights: [],
              final_score: 0.92,
              vector_score: 0.9,
              keyword_score: 0.95,
              topics: ['machine-learning'],
              page: 1,
              has_image: false,
            },
          ],
          total: 1,
          offset: 0,
          limit: 20,
          latency_ms: 45,
          search_mode: 'hybrid',
        }),
      });
    });

    await page.goto('/search');
  });

  test('search page renders the search bar', async ({ authedPage: page }) => {
    const searchInput = page.getByRole('textbox', { name: /search|query/i }).first();
    await expect(searchInput).toBeVisible({ timeout: 10_000 });
  });

  test('entering a query and submitting shows results', async ({ authedPage: page }) => {
    const searchInput = page.getByRole('textbox', { name: /search|query/i }).first();
    await searchInput.fill('machine learning');
    await page.keyboard.press('Enter');

    // Should show result card with the mocked doc title
    await expect(page.getByText('Sample PDF')).toBeVisible({ timeout: 10_000 });
  });

  test('score chip shows percentage', async ({ authedPage: page }) => {
    const searchInput = page.getByRole('textbox', { name: /search|query/i }).first();
    await searchInput.fill('test');
    await page.keyboard.press('Enter');

    // 0.92 → 92%
    await expect(page.getByText('92%')).toBeVisible({ timeout: 10_000 });
  });

  test('result text is visible in the card', async ({ authedPage: page }) => {
    const searchInput = page.getByRole('textbox', { name: /search|query/i }).first();
    await searchInput.fill('sample');
    await page.keyboard.press('Enter');

    await expect(page.getByText(/sample search result/i)).toBeVisible({ timeout: 10_000 });
  });
});
