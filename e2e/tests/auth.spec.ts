/**
 * E2E: Authentication flow
 *
 * Tests the login page, Google OAuth button visibility,
 * and the redirect to /dashboard after successful auth injection.
 */
import { test, expect } from '@playwright/test';
import { loginWithDevToken } from './fixtures';

test.describe('Authentication', () => {
  test('landing page shows Google login button', async ({ page }) => {
    await page.goto('/');
    // The Landing page should display the app name and a login button
    await expect(page).toHaveTitle(/Knowledge Graph|KG/i);
    // Google OAuth button or sign-in CTA present
    const loginEl = page.getByRole('button', { name: /sign in|login|google/i }).first();
    await expect(loginEl).toBeVisible({ timeout: 10_000 });
  });

  test('injecting dev token redirects to dashboard', async ({ page }) => {
    await loginWithDevToken(page);
    await page.goto('/dashboard');
    // Should NOT be redirected back to landing
    await expect(page).toHaveURL(/dashboard/);
    // NavBar should show user controls
    await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 10_000 });
  });

  test('unauthenticated user is redirected to landing', async ({ page }) => {
    // Clear any existing auth state
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/dashboard');
    // RequireAuth should redirect to /
    await expect(page).toHaveURL(/^\//);
    await expect(page.getByRole('button', { name: /sign in|login|google/i }).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('logout clears session and returns to landing', async ({ page }) => {
    await loginWithDevToken(page);
    await page.goto('/dashboard');
    await page.getByRole('button', { name: /logout/i }).click();
    await expect(page).toHaveURL(/^\//);
  });
});
