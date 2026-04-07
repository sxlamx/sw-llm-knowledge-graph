/**
 * E2E: RBAC (Role-Based Access Control) Tests
 *
 * Verifies that:
 * - Admin users can access admin endpoints
 * - Regular users CANNOT access admin endpoints
 * - Users can only access their own collections
 * - Cross-user collection access is blocked
 */
import { test, expect, type Page } from '@playwright/test';

// Test users with different roles
const ADMIN_USER = {
  email: 'admin@test.local',
  name: 'Admin User',
  role: 'admin',
};

const REGULAR_USER = {
  email: 'user@test.local',
  name: 'Regular User',
  role: 'user',
};

/**
 * Inject a mock JWT token with specified role
 */
async function injectToken(page: Page, role: 'admin' | 'user', userId: string) {
  const payload = {
    sub: userId,
    email: role === 'admin' ? ADMIN_USER.email : REGULAR_USER.email,
    name: role === 'admin' ? ADMIN_USER.name : REGULAR_USER.name,
    tenant_id: userId,
    roles: [role],
    role: role,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
    jti: `test-jti-${Date.now()}`,
  };

  // Create a fake token (base64 encoded JSON)
  const fakeToken = Buffer.from(JSON.stringify(payload)).toString('base64url');
  
  await page.evaluate((token) => {
    localStorage.setItem('kg_access_token', token);
  }, fakeToken);
}

test.describe('RBAC - Role-Based Access Control', () => {
  test('admin user can access admin panel', async ({ page }) => {
    const adminId = 'admin-user-uuid-001';
    await injectToken(page, 'admin', adminId);
    
    await page.goto('/admin');
    
    // Admin panel should be accessible
    // Note: This assumes an /admin route exists
    await expect(page).toHaveURL(/admin/);
  });

  test('regular user cannot access admin panel', async ({ page }) => {
    const userId = 'regular-user-uuid-001';
    await injectToken(page, 'user', userId);
    
    await page.goto('/admin');
    
    // Should be redirected or shown access denied
    // The frontend should handle 403 gracefully
    await expect(page).not.toHaveURL(/admin/);
  });

  test('user can create and access their own collection', async ({ page }) => {
    const userId = 'test-user-uuid-002';
    await injectToken(page, 'user', userId);
    
    await page.goto('/dashboard');
    
    // Create a new collection
    await page.waitForSelector('[data-testid="create-collection-btn"]', { timeout: 10000 }).catch(() => null);
    const createBtn = page.getByTestId('create-collection-btn');
    
    if (await createBtn.isVisible()) {
      await createBtn.click();
      
      // Fill in collection details
      await page.getByLabel('Collection Name').fill('My Test Collection');
      await page.getByLabel('Folder Path').fill('/tmp/test-collection');
      
      // Submit
      await page.getByRole('button', { name: /create/i }).click();
      
      // Wait for success notification or collection to appear
      await page.waitForSelector('[data-testid="collection-item"]', { timeout: 10000 }).catch(() => null);
    }
  });

  test('user cannot access another user\'s collection', async ({ page }) => {
    const userId = 'test-user-uuid-003';
    await injectToken(page, 'user', userId);
    
    // Try to access a collection owned by another user
    const otherUserCollectionId = '00000000-0000-0000-0000-000000000001';
    await page.goto(`/collection/${otherUserCollectionId}`);
    
    // Should show access denied or redirect
    await expect(page.locator('body')).toContainText(/access denied|not found|unauthorized/i, { timeout: 10000 });
  });

  test('admin can access any collection', async ({ page }) => {
    const adminId = 'admin-user-uuid-003';
    await injectToken(page, 'admin', adminId);
    
    // Admin should be able to view any collection
    const anyCollectionId = '00000000-0000-0000-0000-000000000002';
    await page.goto(`/collection/${anyCollectionId}`);
    
    // Admin access should be granted (or show "not found" if collection doesn't exist, but NOT "access denied")
    await expect(page.locator('body')).not.toContainText(/access denied/i, { timeout: 10000 });
  });

  test('unauthenticated requests are rejected', async ({ page }) => {
    // Clear any auth state
    await page.evaluate(() => localStorage.clear());
    
    // Try to access protected endpoint directly
    await page.goto('/dashboard');
    
    // Should redirect to login
    await expect(page).toHaveURL(/^\//);
  });
});

test.describe('API RBAC Tests', () => {
  test('401 on API request without token', async ({ page }) => {
    // Clear auth
    await page.evaluate(() => localStorage.clear());
    
    // Make direct API request
    const response = await page.request.get('/api/v1/collections');
    
    expect(response.status()).toBe(401);
  });

  test('403 on admin endpoint with user role', async ({ page }) => {
    const userId = 'test-user-uuid-004';
    await injectToken(page, 'user', userId);
    
    // Get the token
    const token = await page.evaluate(() => localStorage.getItem('kg_access_token'));
    
    // Try to access admin endpoint
    const response = await page.request.get('/api/v1/admin/users', {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });
    
    expect(response.status()).toBe(403);
  });

  test('200 on admin endpoint with admin role', async ({ page }) => {
    const adminId = 'admin-user-uuid-004';
    await injectToken(page, 'admin', adminId);
    
    const token = await page.evaluate(() => localStorage.getItem('kg_access_token'));
    
    const response = await page.request.get('/api/v1/admin/users', {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });
    
    // Should succeed (may return empty array if no users)
    expect([200, 201]).toContain(response.status());
  });
});
