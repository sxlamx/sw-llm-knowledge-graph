/**
 * E2E test fixtures and utilities.
 */
import { type Page } from '@playwright/test';

/**
 * Inject a dev token into localStorage and reload.
 */
export async function loginWithDevToken(page: Page) {
  await page.evaluate(() => {
    localStorage.setItem('kg_access_token', 'dev_token_test_user');
  });
}

/**
 * Inject a mock JWT token with specified role and user ID.
 */
export async function injectMockToken(page: Page, role: 'admin' | 'user', userId: string) {
  const payload = {
    sub: userId,
    email: `${role}@test.local`,
    name: `${role === 'admin' ? 'Admin' : 'Test'} User`,
    tenant_id: userId,
    roles: [role],
    role: role,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
    jti: `test-jti-${Date.now()}`,
  };

  const fakeToken = Buffer.from(JSON.stringify(payload)).toString('base64url');
  
  await page.evaluate((token) => {
    localStorage.setItem('kg_access_token', token);
  }, fakeToken);
}

// ---------------------------------------------------------------------------
// Extended test fixture
// ---------------------------------------------------------------------------

interface E2EFixtures {
  authedPage: Page;
}

export const test = base.extend<E2EFixtures>({
  authedPage: async ({ page }, use) => {
    await loginWithDevToken(page);
    await page.goto('/dashboard');
    await use(page);
  },
});

export { expect };
