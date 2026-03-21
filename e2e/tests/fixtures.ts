/**
 * Shared Playwright fixtures and helpers.
 *
 * The tests use a mock Google OAuth flow:  instead of going to accounts.google.com,
 * the API stub (or a test-only endpoint) accepts a preset token and issues a JWT.
 * Set E2E_DEV_TOKEN=dev_token_xxx in your environment before running.
 */
import { test as base, Page, expect } from '@playwright/test';

export const DEV_TOKEN = process.env.E2E_DEV_TOKEN ?? 'dev_token_e2e';
export const API_BASE  = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1';

// ---------------------------------------------------------------------------
// Auth helper: inject a dev JWT directly into Redux store via localStorage
// ---------------------------------------------------------------------------

export async function loginWithDevToken(page: Page): Promise<void> {
  // The frontend reads `persist:auth` from localStorage (Redux Persist).
  // We write a minimal state that satisfies RequireAuth: accessToken + user.
  const authState = JSON.stringify({
    accessToken: DEV_TOKEN,
    refreshToken: 'dev_refresh_e2e',
    user: {
      id: 'dev-user',
      email: 'e2e@example.com',
      name: 'E2E User',
      picture: '',
    },
    isLoading: false,
    error: null,
  });

  await page.goto('/');
  await page.evaluate(
    ([key, value]) => localStorage.setItem(key, value),
    ['persist:auth', JSON.stringify({ auth: authState, _persist: '{"version":-1,"rehydrated":true}' })],
  );
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
