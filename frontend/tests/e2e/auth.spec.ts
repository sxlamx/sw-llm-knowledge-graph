import { test, expect } from './fixtures';

test.describe('Auth flow', () => {
  test('login via OAuth callback redirects to dashboard', async ({ authenticatedPage }) => {
    await expect(authenticatedPage).toHaveURL(/\/dashboard/);
    await expect(authenticatedPage.getByText('My Collections')).toBeVisible();
  });

  test('dashboard shows collections after login', async ({ authenticatedPage }) => {
    await expect(authenticatedPage.getByText('E2E Collection')).toBeVisible();
  });

  test('page reload maintains auth state', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/v1/collections', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ collections: [
          { id: 'e2e-col-1', name: 'E2E Collection', doc_count: 3, status: 'active' },
        ]}),
      })
    );

    await authenticatedPage.reload();
    await expect(authenticatedPage).toHaveURL(/\/dashboard/);
    await expect(authenticatedPage.getByText('My Collections')).toBeVisible();
  });

  test('unauthenticated user sees login page at /', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Knowledge Graph Builder')).toBeVisible();
    await expect(page.getByText('Sign in with Google')).toBeVisible();
  });
});