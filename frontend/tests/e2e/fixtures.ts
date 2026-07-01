import { test as base, expect } from '@playwright/test';

const mockUser = { id: 'e2e-user', email: 'e2e@test.com', name: 'E2E Tester' };

type Fixtures = {
  authenticatedPage: import('@playwright/test').Page;
};

export const test = base.extend<Fixtures>({
  authenticatedPage: async ({ page }, use) => {
    await page.route('**/api/v1/auth/google/exchange', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ access_token: 'e2e-test-token', token_type: 'bearer', expires_in: 3600, user: mockUser }),
      })
    );

    await page.route('**/api/v1/collections', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ collections: [
          { id: 'e2e-col-1', name: 'E2E Collection', doc_count: 3, status: 'active', created_at: '2026-01-01T00:00:00Z' },
        ]}),
      })
    );

    await page.goto('/auth/callback/google?code=test-code');
    await page.waitForURL('**/dashboard');
    await use(page);
  },
});

export { expect };