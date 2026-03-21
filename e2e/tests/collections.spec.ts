/**
 * E2E: Collections dashboard
 *
 * Verifies that the dashboard loads, collections are displayed or a create
 * button is present, and that a new collection can be initiated.
 */
import { test, expect } from './fixtures';

test.describe('Collections dashboard', () => {
  test('dashboard renders collection list or empty state', async ({ authedPage: page }) => {
    // Either the DataGrid or an empty-state message should be present
    const hasGrid = page.locator('[role="grid"]');
    const hasEmpty = page.getByText(/no collections|create your first/i);

    await expect(hasGrid.or(hasEmpty)).toBeVisible({ timeout: 15_000 });
  });

  test('create collection button is present', async ({ authedPage: page }) => {
    const createBtn = page.getByRole('button', { name: /new collection|create collection|\+/i }).first();
    await expect(createBtn).toBeVisible({ timeout: 10_000 });
  });

  test('clicking create collection opens dialog', async ({ authedPage: page }) => {
    const createBtn = page.getByRole('button', { name: /new collection|create collection|\+/i }).first();
    await createBtn.click();
    // A dialog or input for collection name should appear
    const nameInput = page.getByRole('textbox', { name: /name/i }).first();
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
  });

  test('dark mode toggle changes theme', async ({ authedPage: page }) => {
    const toggleBtn = page.getByRole('button', { name: /toggle theme/i });
    await expect(toggleBtn).toBeVisible({ timeout: 5_000 });

    // Record current background color
    const bgBefore = await page.evaluate(() =>
      getComputedStyle(document.body).backgroundColor
    );

    await toggleBtn.click();

    const bgAfter = await page.evaluate(() =>
      getComputedStyle(document.body).backgroundColor
    );

    // Background should change between light and dark
    expect(bgBefore).not.toBe(bgAfter);
  });

  test('navbar shows knowledge graph title', async ({ authedPage: page }) => {
    await expect(page.getByText(/Knowledge Graph Builder/i).first()).toBeVisible({ timeout: 5_000 });
  });
});
