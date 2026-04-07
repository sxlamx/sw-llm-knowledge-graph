# Bot 3 — Test: Phase 5 — React Frontend

## Your Role

QA engineer writing component tests (vitest + testing-library) and E2E tests (Playwright)
for the React frontend.

---

## Test Frameworks

- **Unit/Component**: `vitest` + `@testing-library/react` + `@testing-library/user-event`
- **E2E**: `Playwright` (chromium + webkit)
- **API mocking**: `msw` (Mock Service Worker) for REST; mock WebSocket server for WS tests
- **RTK Query mocking**: Use `setupServer` from `msw` with handlers per test

---

## Test File Locations

```
frontend/
  src/
    components/
      graph/ForceGraph.test.tsx    ← color mapping, label toggle
      search/SearchBar.test.tsx    ← debounce, mode selector
      auth/RequireAuth.test.tsx    ← redirect unauthenticated
    store/
      authSlice.test.ts            ← localStorage persistence
      baseApi.test.ts              ← reauth flow (401 → refresh → retry)
    pages/
      Dashboard.test.tsx           ← collections load, create, delete
      SearchPage.test.tsx          ← search modes, topic filter
  tests/e2e/
    auth.spec.ts                   ← login → dashboard → reload
    ingest.spec.ts                 ← create collection → ingest → view graph
    search.spec.ts                 ← search all modes
    graph.spec.ts                  ← graph viewer, node click, label toggle
```

---

## Critical Test Cases

### Auth Slice (`authSlice.test.ts`)

```typescript
describe('authSlice localStorage persistence', () => {
  beforeEach(() => localStorage.clear());

  it('persists accessToken on setCredentials', () => {
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(setCredentials({ accessToken: 'test-token', user: mockUser }));
    expect(localStorage.getItem('kg_access_token')).toBe('test-token');
  });

  it('persists accessToken on setAccessToken', () => {
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(setAccessToken('refresh-token'));
    expect(localStorage.getItem('kg_access_token')).toBe('refresh-token');
  });

  it('restores token from localStorage on init', () => {
    localStorage.setItem('kg_access_token', 'stored-token');
    localStorage.setItem('kg_user', JSON.stringify(mockUser));
    const store = configureStore({ reducer: { auth: authReducer } });
    expect(store.getState().auth.accessToken).toBe('stored-token');
    expect(store.getState().auth.isAuthenticated).toBe(true);
  });

  it('clears localStorage on clearCredentials', () => {
    localStorage.setItem('kg_access_token', 'token');
    localStorage.setItem('kg_user', JSON.stringify(mockUser));
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(clearCredentials());
    expect(localStorage.getItem('kg_access_token')).toBeNull();
    expect(localStorage.getItem('kg_user')).toBeNull();
  });
});
```

### Base API Reauth (`baseApi.test.ts`)

```typescript
it('dispatches setAccessToken when user is null after refresh', async () => {
  // Simulate page reload: user is null, token in localStorage
  // First call → 401, refresh → 200 with new token
  // Verify setAccessToken dispatched (not just setCredentials)
  server.use(
    rest.get('/api/v1/collections', (req, res, ctx) =>
      res.once(ctx.status(401))),
    rest.post('/api/v1/auth/refresh', (req, res, ctx) =>
      res(ctx.json({ access_token: 'new-token' }))),
    rest.get('/api/v1/collections', (req, res, ctx) =>
      res(ctx.json([]))),
  );
  // Run request with null user in state
  // Assert setAccessToken called with 'new-token'
});
```

### ForceGraph Colors (`ForceGraph.test.tsx`)

```typescript
it('uses ORGANIZATION color for org nodes (not ORG)', () => {
  expect(ENTITY_TYPE_COLORS['ORGANIZATION']).toBeDefined();
  expect(ENTITY_TYPE_COLORS['ORG']).toBeUndefined(); // spaCy shorthand should NOT exist
});

it('uses LOCATION color for location nodes (not GPE)', () => {
  expect(ENTITY_TYPE_COLORS['LOCATION']).toBeDefined();
  expect(ENTITY_TYPE_COLORS['GPE']).toBeUndefined();
});

it('applies correct color based on entity_type', () => {
  const node = { id: '1', entity_type: 'ORGANIZATION', label: 'OpenAI' };
  const color = ENTITY_TYPE_COLORS[node.entity_type] ?? '#999';
  expect(color).toBe('#2196F3');  // blue for organization
});

it('renders node labels when showLabels=true', () => {
  // Render ForceGraph with showLabels=true and mock graph data
  // Verify nodeCanvasObjectMode returns 'after' (labels rendered)
});
```

### RequireAuth (`RequireAuth.test.tsx`)

```typescript
it('redirects unauthenticated user to login', () => {
  const store = configureStore({ reducer: { auth: authReducer } });
  // auth.isAuthenticated = false
  const { container } = render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route path="/dashboard" element={<RequireAuth><Dashboard /></RequireAuth>} />
          <Route path="/" element={<div>Login</div>} />
        </Routes>
      </MemoryRouter>
    </Provider>
  );
  expect(container.textContent).toContain('Login');
});
```

### E2E: Full Login Flow (`tests/e2e/auth.spec.ts`)

```typescript
test('login → dashboard → reload maintains auth', async ({ page }) => {
  // Mock Google OAuth callback
  await page.route('**/api/v1/auth/google', route =>
    route.fulfill({ json: { access_token: 'tok', user: mockUser } }));

  await page.goto('/');
  await page.click('[data-testid="google-login-btn"]');
  await page.waitForURL('**/dashboard');
  expect(await page.title()).toContain('Dashboard');

  // Reload — should stay authenticated
  await page.reload();
  await page.waitForURL('**/dashboard');
  expect(page.url()).toContain('/dashboard');
});
```

### E2E: Graph Viewer (`tests/e2e/graph.spec.ts`)

```typescript
test('graph nodes have correct colors by entity type', async ({ page }) => {
  await authenticateAndNavigate(page, '/graph/test-collection');
  // Verify canvas renders (can check that ForceGraph component exists)
  const graph = page.locator('[data-testid="force-graph"]');
  await expect(graph).toBeVisible();
});

test('label toggle shows and hides node labels', async ({ page }) => {
  await authenticateAndNavigate(page, '/graph/test-collection');
  const toggleBtn = page.locator('[data-testid="label-toggle-btn"]');
  await toggleBtn.click();  // enable labels
  // Verify button shows LabelIcon (active state)
  await expect(toggleBtn).toHaveAttribute('color', 'primary');
});
```

---

## MSW Handlers (shared `src/test/handlers.ts`)

```typescript
export const handlers = [
  rest.get('/api/v1/collections', (req, res, ctx) =>
    res(ctx.json([{ id: 'col-1', name: 'Test', doc_count: 5 }]))),
  rest.post('/api/v1/search', (req, res, ctx) =>
    res(ctx.json({ results: [{ chunk_id: '1', text: 'sample', score: 0.9 }] }))),
  rest.get('/api/v1/graph/subgraph', (req, res, ctx) =>
    res(ctx.json({ nodes: mockNodes, edges: mockEdges }))),
];
```

---

## Coverage Targets

- Component tests: all page components, all auth flow branches
- E2E: login → ingest → search → graph viewer (full happy path)
- Auth negative cases: unauthenticated redirect, token expiry, refresh failure
