# Bot 1 — Build: Phase 5 — React Frontend

## Your Role

You are a senior frontend engineer implementing the React 18 + TypeScript + MUI v6 frontend for
`sw-llm-knowledge-graph`. This covers the auth flow, collections UI, search, and graph viewer.

---

## Project Context

- **Framework**: React 18 + TypeScript + Vite + Material UI v6
- **State**: Redux Toolkit + RTK Query (data fetching + caching)
- **Graph**: `react-force-graph-2d` (canvas-based, NOT SVG)
- **Auth**: Google OAuth 2.0 → access token stored in Redux + `localStorage` key `kg_access_token`
- **WebSocket**: Redux middleware dispatches RTK tag invalidation on `graph_update` messages

**Read these specs before writing any code:**
- `specifications/09-frontend-design.md` — routing, component specs, state management, auth flow
- `specifications/08-api-design.md` — REST endpoint contracts, WebSocket message types
- `specifications/10-auth-security.md` — JWT storage, refresh flow, cookie security

---

## LESSONS.md Rules (Non-Negotiable)

1. **Access token localStorage**: Persist `kg_access_token` to `localStorage` on login/refresh.
   Restore on app init. Clear on logout. This prevents 401 flash on page reload.
   NOT memory-only as originally specced.
2. **Canonical NER colors**: `ENTITY_TYPE_COLORS` map must use canonical labels:
   `ORGANIZATION` (not `ORG`), `LOCATION` (not `GPE`). Graph nodes are stored with canonical types.
3. **RTK reauth**: When `state.auth.user` is null after refresh (page reload case), dispatch
   `setAccessToken(data.access_token)` — not only `setCredentials`. Both cases must be handled.

---

## Implementation Tasks

### 1. Auth slice (`src/store/slices/authSlice.ts`)

```typescript
interface AuthState {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
}

// Initial state: restore from localStorage
const storedUser = localStorage.getItem('kg_user');
const storedToken = localStorage.getItem('kg_access_token');
const initialState: AuthState = {
  user: storedUser ? JSON.parse(storedUser) : null,
  accessToken: storedToken ?? null,
  isAuthenticated: !!storedUser,
};

// setCredentials: save both user and token to localStorage
// setAccessToken: save token to localStorage (when user already set, e.g. after page reload refresh)
// clearCredentials: remove both from localStorage
```

Actions: `setCredentials({ accessToken, user })`, `setAccessToken(token)`, `clearCredentials()`

### 2. Base API with reauth (`src/api/baseApi.ts`)

```typescript
const baseQueryWithReauth = async (args, api, extraOptions) => {
  let result = await baseQuery(args, api, extraOptions);

  if (result.error?.status === 401) {
    // Try to refresh
    const refreshResult = await baseQuery(
      { url: '/auth/refresh', method: 'POST' },
      api,
      extraOptions
    );

    if (refreshResult.data) {
      const { access_token } = refreshResult.data as { access_token: string };
      const state = api.getState() as RootState;

      if (state.auth.user) {
        api.dispatch(setCredentials({ accessToken: access_token, user: state.auth.user }));
      } else {
        // Page reload case: user not in Redux yet, but token restored from localStorage
        api.dispatch(setAccessToken(access_token));
      }
      // Retry original request
      result = await baseQuery(args, api, extraOptions);
    } else {
      api.dispatch(clearCredentials());
    }
  }
  return result;
};
```

Bearer token attached: `headers['Authorization'] = \`Bearer ${token}\``

### 3. Google OAuth flow (`src/pages/LoginPage.tsx`)

1. Show "Sign in with Google" button
2. On click: redirect to `GET /api/v1/auth/google/authorize` or use Google Identity Services popup
3. `CallbackPage.tsx` handles redirect with `?code=xxx` — calls `POST /api/v1/auth/google`
4. Store returned `access_token` + user info, redirect to `/dashboard`

### 4. Collections page (`src/pages/Dashboard.tsx`)

- MUI DataGrid listing user's collections (name, doc_count, status, created_at)
- "New Collection" button: dialog with name + folder_path inputs
- Delete collection: confirm dialog → `DELETE /collections/{id}`
- Click row → navigate to `/collection/:id`

### 5. Collection page (`src/pages/CollectionPage.tsx`)

- Document list (MUI DataGrid)
- `IngestPanel.tsx` component: folder path input → `POST /ingest/folder` → show job progress
- Job status polling via `GET /ingest/jobs/{id}` (500ms interval)
- "View Graph" button → navigate to `/graph/:collectionId`

### 6. Search page (`src/pages/SearchPage.tsx`)

- Search mode selector: tabs or chips for `hybrid | vector | keyword | graph`
- `SearchBar.tsx`: debounced input (300ms), triggers `POST /search`
- `TopicSidebar.tsx`: multi-select topic chips from `GET /topics?collection_id=xxx`
- `SearchResults.tsx`: list of `ResultCard.tsx` (text excerpt, doc title, page number, score)

### 7. Graph viewer (`src/pages/GraphViewer.tsx`)

- `react-force-graph-2d` with canvas rendering (NOT SVG)
- `ENTITY_TYPE_COLORS` map with canonical labels:

```typescript
export const ENTITY_TYPE_COLORS: Record<string, string> = {
  PERSON:       '#4CAF50',
  ORGANIZATION: '#2196F3',   // NOT 'ORG'
  LOCATION:     '#FF9800',   // NOT 'GPE' or 'LOC'
  LAW:          '#607D8B',
  DATE:         '#78909C',
  MONEY:        '#8BC34A',
  PERCENT:      '#B0BEC5',
  COURT_CASE:           '#9C27B0',
  LEGISLATION_TITLE:    '#3F51B5',
  LEGISLATION_REFERENCE:'#00BCD4',
  COURT:                '#FF5722',
  JUDGE:                '#795548',
};
```

- `showLabels` toggle (LabelIcon/LabelOffIcon in toolbar): when true, render node name text on canvas
- `GraphControls.tsx`: depth slider (1-4), edge type filter chips
- `NodeDetailPanel.tsx`: MUI Drawer, entity properties, linked chunks, edit form
- Web worker for force layout: `workers/graphLayout.worker.ts` runs D3 force 300 ticks synchronously

### 8. WebSocket middleware (`src/store/wsMiddleware.ts`)

```typescript
// On 'graph_update' message: invalidate RTK Query graph tags
api.dispatch(graphApi.util.invalidateTags(['Graph', 'Node']));

// On 'job_progress' message: update ingest job status in Redux
api.dispatch(updateJobStatus({ jobId, status, progress }));
```

Connect to `WS /api/v1/ws` with JWT token as query param.

### 9. Code splitting (`src/App.tsx`)

```typescript
const Dashboard = lazy(() => import('./pages/Dashboard'));
const GraphViewer = lazy(() => import('./pages/GraphViewer'));
const SearchPage = lazy(() => import('./pages/SearchPage'));
const OntologyEditor = lazy(() => import('./pages/OntologyEditor'));

// Wrap in <Suspense fallback={<LoadingOverlay />}>
```

### 10. RequireAuth guard (`src/components/auth/RequireAuth.tsx`)

```typescript
const RequireAuth = ({ children }) => {
  const { isAuthenticated } = useSelector(state => state.auth);
  return isAuthenticated ? children : <Navigate to="/" replace />;
};
```

---

## Constraints

- Canvas-based graph rendering (not SVG) — react-force-graph-2d uses HTMLCanvas
- ENTITY_TYPE_COLORS must use canonical labels (ORGANIZATION, LOCATION) — never spaCy shorthand
- Access token in Redux + localStorage — never sessionStorage, never absent from localStorage
- All page components code-split with `React.lazy()`
- No direct DOM manipulation outside React

---

## Acceptance Criteria

1. Google OAuth login → lands on `/dashboard` with user info in Redux
2. Page reload → `isAuthenticated=true`, collections load without 401 flash
3. Collections DataGrid shows user's collections from API
4. Ingest job progress visible in collection page
5. Search with all 4 modes returns results (hybrid, vector, keyword, graph)
6. Graph viewer renders nodes with correct colors by canonical entity_type
7. Node label toggle (LabelIcon) shows/hides canvas text labels
8. NodeDetailPanel opens on node click with properties and edit form
9. WebSocket `graph_update` message triggers RTK Query cache invalidation
10. All pages lazy-loaded; no eager imports for non-critical pages
