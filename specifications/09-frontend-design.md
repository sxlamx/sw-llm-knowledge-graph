# 09 — Frontend Design

## 1. Technology Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| React | 18.x | UI framework (concurrent rendering) |
| TypeScript | 5.x | Type safety |
| Vite.js | 5.x | Build tool and dev server |
| Material UI | v6 | Component library |
| Redux Toolkit | 2.x | Global state management |
| RTK Query | (included in RTK) | API data fetching, caching, invalidation |
| `@react-oauth/google` | latest | Google OAuth 2.0 PKCE flow |
| `react-force-graph-2d` | latest | Canvas-based graph visualization |
| Cytoscape.js | 3.x | Alternative graph library (richer layout algorithms) |
| `react-window` | 1.x | Virtualized lists for large result sets |
| `react-router-dom` | v6 | Client-side routing |

---

## 2. Application Routes

```
/                      → Landing page with "Sign in with Google" button
/dashboard             → Collections dashboard (list, create, delete collections)
/collection/:id        → Collection detail view (documents, ingest controls, progress)
/search                → Search interface (query bar, results, graph preview)
/graph/:collectionId   → Full-screen interactive graph viewer
/ontology/:collectionId → Ontology editor (entity types, relationship types)
/settings              → User settings (API cost limits, preferences)
```

All routes except `/` require authentication. Protected by a `RequireAuth` wrapper component that
redirects to `/` if the user is not authenticated.

---

## 3. Application Architecture

```
src/
├── main.tsx                    # React root, Redux Provider, GoogleOAuthProvider
├── App.tsx                     # Router, theme setup, global error boundary
│
├── store/                      # Redux Toolkit
│   ├── index.ts                # configureStore (root reducer + RTK Query middleware)
│   ├── slices/
│   │   ├── authSlice.ts        # auth state: user, token, loading
│   │   ├── collectionsSlice.ts # active collection, collection list
│   │   ├── searchSlice.ts      # search state: query, mode, weights, filters
│   │   ├── graphSlice.ts       # selected node, path finding mode, depth
│   │   └── uiSlice.ts          # drawer open, sidebar state, theme mode
│   └── wsMiddleware.ts         # WebSocket middleware for real-time events
│
├── api/                        # RTK Query
│   ├── baseApi.ts              # createApi, baseQuery with JWT auto-attach + refresh
│   ├── authApi.ts              # /auth/* endpoints
│   ├── collectionsApi.ts       # /collections/* endpoints
│   ├── ingestApi.ts            # /ingest/* endpoints
│   ├── searchApi.ts            # /search endpoint
│   ├── graphApi.ts             # /graph/* endpoints
│   ├── ontologyApi.ts          # /ontology/* endpoints
│   └── documentsApi.ts         # /documents/* endpoints
│
├── pages/
│   ├── Landing.tsx
│   ├── Dashboard.tsx
│   ├── Collection.tsx
│   ├── Search.tsx
│   ├── GraphViewer.tsx
│   ├── OntologyEditor.tsx
│   └── Settings.tsx
│
├── components/
│   ├── auth/
│   │   ├── GoogleLoginButton.tsx
│   │   └── RequireAuth.tsx
│   ├── graph/
│   │   ├── ForceGraph.tsx      # react-force-graph-2d wrapper
│   │   ├── NodeDetailPanel.tsx # MUI Drawer with entity details
│   │   ├── PathFinder.tsx      # Two-node selection for shortest path
│   │   └── GraphControls.tsx   # Depth slider, filter chips, layout selector
│   ├── search/
│   │   ├── SearchBar.tsx       # Query input + mode selector
│   │   ├── ResultCard.tsx      # Individual search result with highlights
│   │   ├── TopicSidebar.tsx    # Topic multi-select filter
│   │   └── SearchResults.tsx   # Virtualized result list
│   ├── ingest/
│   │   ├── IngestPanel.tsx     # Folder picker + start button
│   │   ├── ProgressBar.tsx     # SSE-driven progress
│   │   └── JobStatusChip.tsx   # Status badge
│   └── common/
│       ├── Layout.tsx          # AppBar + Drawer + main content area
│       ├── NavBar.tsx          # Top navigation
│       ├── ThemeProvider.tsx   # MUI theme (dark/light)
│       ├── ErrorBoundary.tsx
│       └── LoadingOverlay.tsx
│
└── workers/
    └── graphLayout.worker.ts   # Web Worker: force-directed layout computation
```

---

## 4. Auth Flow

```
1. User visits /
   └─► Landing.tsx renders <GoogleLoginButton />

2. User clicks "Sign in with Google"
   └─► @react-oauth/google opens Google consent popup (PKCE flow)
   └─► Google returns ID token to frontend callback

3. Frontend: POST /api/v1/auth/google { id_token }
   └─► Backend validates, returns { access_token, user }
   └─► Backend sets HttpOnly refresh_token cookie

4. Frontend:
   └─► Store access_token in Redux (memory only — NOT localStorage)
   └─► Store user in Redux + localStorage (for UI persistence)
   └─► Navigate to /dashboard

5. All API calls via RTK Query baseQuery:
   └─► Auto-attach: Authorization: Bearer <access_token>
   └─► On 401: call POST /auth/refresh → get new access_token
   └─► Retry original request with new token
   └─► On refresh failure: dispatch logout() → redirect to /
```

### Auth Slice

```typescript
// store/slices/authSlice.ts
interface AuthState {
  user: User | null;
  accessToken: string | null;  // in memory only
  isAuthenticated: boolean;
  isLoading: boolean;
}

const authSlice = createSlice({
  name: 'auth',
  initialState: { user: null, accessToken: null, isAuthenticated: false, isLoading: false },
  reducers: {
    setCredentials: (state, action: PayloadAction<{ user: User; accessToken: string }>) => {
      state.user = action.payload.user;
      state.accessToken = action.payload.accessToken;
      state.isAuthenticated = true;
    },
    logout: (state) => {
      state.user = null;
      state.accessToken = null;
      state.isAuthenticated = false;
    },
  },
});
```

---

## 5. RTK Query Base API

```typescript
// api/baseApi.ts
import { createApi, fetchBaseQuery, BaseQueryFn } from '@reduxjs/toolkit/query/react';

const baseQuery = fetchBaseQuery({
  baseUrl: import.meta.env.VITE_API_BASE_URL,
  prepareHeaders: (headers, { getState }) => {
    const token = (getState() as RootState).auth.accessToken;
    if (token) headers.set('Authorization', `Bearer ${token}`);
    return headers;
  },
  credentials: 'include',  // sends HttpOnly cookie for refresh
});

// Wrapper with automatic token refresh on 401
const baseQueryWithReauth: BaseQueryFn = async (args, api, extraOptions) => {
  let result = await baseQuery(args, api, extraOptions);
  if (result.error?.status === 401) {
    // Attempt token refresh
    const refreshResult = await baseQuery({ url: '/auth/refresh', method: 'POST' }, api, extraOptions);
    if (refreshResult.data) {
      const { access_token } = refreshResult.data as { access_token: string };
      api.dispatch(setCredentials({ ...selectCurrentUser(api.getState()), accessToken: access_token }));
      result = await baseQuery(args, api, extraOptions);
    } else {
      api.dispatch(logout());
    }
  }
  return result;
};

export const api = createApi({
  reducerPath: 'api',
  baseQuery: baseQueryWithReauth,
  tagTypes: ['Collection', 'Document', 'IngestJob', 'SearchResult', 'GraphNode', 'Ontology', 'Topic'],
  endpoints: () => ({}),
});
```

---

## 6. Key Components

### 6.1 CollectionsDashboard

```typescript
// pages/Dashboard.tsx
import { DataGrid, GridColDef } from '@mui/x-data-grid';

const columns: GridColDef[] = [
  { field: 'name', headerName: 'Name', flex: 1 },
  { field: 'doc_count', headerName: 'Documents', width: 120, type: 'number' },
  {
    field: 'status',
    headerName: 'Status',
    width: 140,
    renderCell: ({ value }) => (
      <Chip
        label={value}
        color={value === 'active' ? 'success' : value === 'ingesting' ? 'warning' : 'default'}
        size="small"
      />
    ),
  },
  { field: 'created_at', headerName: 'Created', width: 160, type: 'dateTime' },
];
```

### 6.2 IngestPanel

Uses the **File System Access API** for browser-native folder selection:

```typescript
// components/ingest/IngestPanel.tsx

const handleSelectFolder = async () => {
  try {
    // File System Access API — supported in Chrome/Edge, with fallback
    const dirHandle = await (window as any).showDirectoryPicker({ mode: 'read' });
    setFolderPath(dirHandle.name);
    setDirHandle(dirHandle);
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      // Fallback: text input for folder path
      setShowPathInput(true);
    }
  }
};

// SSE progress listener
useEffect(() => {
  if (!activeJobId) return;
  const eventSource = new EventSource(
    `${API_BASE}/ingest/jobs/${activeJobId}/stream`,
    { withCredentials: true }
  );
  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'progress') {
      setProgress(data.progress);
      setCurrentFile(data.current_file);
    }
    if (data.type === 'completed') {
      setProgress(1.0);
      eventSource.close();
      // Invalidate RTK Query cache for this collection
      dispatch(api.util.invalidateTags([{ type: 'Collection', id: collectionId }]));
    }
  };
  return () => eventSource.close();
}, [activeJobId]);
```

### 6.3 SearchBar

```typescript
// components/search/SearchBar.tsx

const SearchBar: React.FC = () => {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>('hybrid');
  const debouncedQuery = useDebounce(query, 300);
  const dispatch = useAppDispatch();

  // Autocomplete suggestions
  const { data: suggestions } = useGetSearchSuggestionsQuery(
    { q: debouncedQuery, collection_id: activeCollectionId },
    { skip: debouncedQuery.length < 2 }
  );

  const handleSearch = () => {
    if (!query.trim()) return;
    dispatch(setSearchQuery(query));
    dispatch(setSearchMode(mode));
    navigate('/search');
  };

  return (
    <Autocomplete
      options={suggestions?.suggestions ?? []}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Search knowledge graph"
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                <ModeSelector value={mode} onChange={setMode} />
                <IconButton onClick={handleSearch}><SearchIcon /></IconButton>
              </>
            ),
          }}
        />
      )}
      onInputChange={(_, value) => setQuery(value)}
    />
  );
};
```

### 6.4 GraphViewer

```typescript
// pages/GraphViewer.tsx

const GraphViewer: React.FC = () => {
  const { collectionId } = useParams<{ collectionId: string }>();
  const [depth, setDepth] = useState(2);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [pathFinderMode, setPathFinderMode] = useState(false);
  const [pathEndpoints, setPathEndpoints] = useState<[string?, string?]>([]);
  const graphWorkerRef = useRef<Worker>();

  const { data: graphData } = useGetGraphDataQuery({ collection_id: collectionId!, page: 0 });

  // Offload layout computation to Web Worker
  useEffect(() => {
    graphWorkerRef.current = new Worker(
      new URL('../workers/graphLayout.worker.ts', import.meta.url),
      { type: 'module' }
    );
    graphWorkerRef.current.onmessage = (e) => setLayoutedGraph(e.data);
    return () => graphWorkerRef.current?.terminate();
  }, []);

  useEffect(() => {
    if (graphData) {
      graphWorkerRef.current?.postMessage({ type: 'layout', graph: graphData });
    }
  }, [graphData]);

  const handleNodeClick = useCallback((node: any) => {
    if (pathFinderMode) {
      setPathEndpoints(prev =>
        prev[0] ? [prev[0], node.id] : [node.id, undefined]
      );
    } else {
      setSelectedNode(node as GraphNode);
    }
  }, [pathFinderMode]);

  return (
    <Box sx={{ display: 'flex', height: '100vh' }}>
      <GraphControls
        depth={depth}
        onDepthChange={setDepth}
        pathFinderMode={pathFinderMode}
        onPathFinderToggle={() => setPathFinderMode(p => !p)}
      />
      <ForceGraph
        graphData={layoutedGraph}
        nodeColor={(node: any) => ENTITY_TYPE_COLORS[node.entity_type] ?? '#888'}
        linkWidth={(link: any) => link.weight * 3}
        onNodeClick={handleNodeClick}
        nodeLabel={(node: any) => node.label}
        maxWidth={5000}
        maxEdges={7000}
      />
      {selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          collectionId={collectionId!}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </Box>
  );
};

// Entity type colors for graph nodes
const ENTITY_TYPE_COLORS: Record<string, string> = {
  Person: '#4CAF50',
  Organization: '#2196F3',
  Location: '#FF9800',
  Concept: '#9C27B0',
  Event: '#F44336',
  Document: '#607D8B',
  Topic: '#00BCD4',
};
```

### 6.5 NodeDetailPanel

```typescript
// components/graph/NodeDetailPanel.tsx

const NodeDetailPanel: React.FC<{ node: GraphNode; collectionId: string; onClose: () => void }> = ({
  node, collectionId, onClose
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const { data: nodeDetail } = useGetGraphNodeQuery({ id: node.id, collection_id: collectionId, depth: 1 });
  const [updateNode] = useUpdateGraphNodeMutation();

  return (
    <Drawer anchor="right" open={true} onClose={onClose} sx={{ width: 400 }}>
      <Box sx={{ p: 2, width: 400 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography variant="h6">{node.label}</Typography>
          <Stack direction="row" spacing={1}>
            <IconButton onClick={() => setIsEditing(e => !e)}><EditIcon /></IconButton>
            <IconButton onClick={onClose}><CloseIcon /></IconButton>
          </Stack>
        </Stack>

        <Chip label={node.entity_type} color="primary" size="small" sx={{ mb: 2 }} />

        {isEditing ? (
          <NodeEditForm node={node} onSave={(update) => {
            updateNode({ id: node.id, collection_id: collectionId, ...update });
            setIsEditing(false);
          }} />
        ) : (
          <>
            <Typography variant="body2" color="text.secondary">{node.description}</Typography>
            <Typography variant="caption">Confidence: {(node.confidence * 100).toFixed(0)}%</Typography>

            <Typography variant="subtitle2" sx={{ mt: 2 }}>Linked Sources</Typography>
            <List dense>
              {nodeDetail?.linked_chunks?.map(chunk => (
                <ListItem key={chunk.chunk_id}>
                  <ListItemText
                    primary={chunk.doc_title}
                    secondary={chunk.text.slice(0, 120) + '...'}
                  />
                </ListItem>
              ))}
            </List>
          </>
        )}
      </Box>
    </Drawer>
  );
};
```

### 6.6 OntologyEditor

```typescript
// pages/OntologyEditor.tsx

const OntologyEditor: React.FC = () => {
  const { collectionId } = useParams<{ collectionId: string }>();
  const { data: ontology } = useGetOntologyQuery({ collection_id: collectionId! });
  const [updateOntology] = useUpdateOntologyMutation();
  const [generateOntology] = useGenerateOntologyMutation();

  return (
    <Grid container spacing={2} sx={{ p: 3 }}>
      {/* Left: Entity type tree */}
      <Grid item xs={5}>
        <Typography variant="h6">Entity Types</Typography>
        <TreeView
          defaultCollapseIcon={<ExpandMoreIcon />}
          defaultExpandIcon={<ChevronRightIcon />}
        >
          {renderEntityTypeTree(ontology?.entity_types)}
        </TreeView>
        <Button startIcon={<AddIcon />} onClick={() => setShowAddEntityDialog(true)}>
          Add Entity Type
        </Button>
      </Grid>

      {/* Right: Relationship table */}
      <Grid item xs={7}>
        <Typography variant="h6">Relationship Types</Typography>
        <DataGrid
          rows={Object.entries(ontology?.relationship_types ?? {}).map(([name, def]) => ({
            id: name, name, domain: def.domain.join(', '), range: def.range.join(', ')
          }))}
          columns={[
            { field: 'name', headerName: 'Name', flex: 1 },
            { field: 'domain', headerName: 'Domain', flex: 1 },
            { field: 'range', headerName: 'Range', flex: 1 },
          ]}
          autoHeight
        />
        <Button
          startIcon={<AutoAwesomeIcon />}
          onClick={() => generateOntology({ collection_id: collectionId! })}
          variant="outlined"
        >
          Generate from Documents
        </Button>
      </Grid>
    </Grid>
  );
};
```

---

## 7. State Management

### Redux Store Structure

```typescript
// store/index.ts
export const store = configureStore({
  reducer: {
    auth: authReducer,
    collections: collectionsReducer,
    search: searchReducer,
    graph: graphReducer,
    ui: uiReducer,
    [api.reducerPath]: api.reducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware()
      .concat(api.middleware)
      .concat(wsMiddleware),
});
```

### WebSocket Middleware

```typescript
// store/wsMiddleware.ts
const wsMiddleware: Middleware = (store) => {
  let ws: WebSocket | null = null;

  return (next) => (action) => {
    if (action.type === 'ws/connect') {
      const token = store.getState().auth.accessToken;
      ws = new WebSocket(`${WS_BASE_URL}?token=${token}`);

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        switch (msg.type) {
          case 'progress':
            store.dispatch(updateJobProgress(msg));
            break;
          case 'graph_update':
            store.dispatch(api.util.invalidateTags([
              { type: 'GraphNode', id: msg.collection_id }
            ]));
            break;
          case 'job_completed':
            store.dispatch(api.util.invalidateTags(['Collection', 'Document']));
            break;
        }
      };
    }
    return next(action);
  };
};
```

---

## 8. Performance Considerations

### Code Splitting

```typescript
// App.tsx — lazy load all pages
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Collection = lazy(() => import('./pages/Collection'));
const Search = lazy(() => import('./pages/Search'));
const GraphViewer = lazy(() => import('./pages/GraphViewer'));
const OntologyEditor = lazy(() => import('./pages/OntologyEditor'));

// Wrap routes in Suspense
<Suspense fallback={<LoadingOverlay />}>
  <Routes>
    <Route path="/dashboard" element={<Dashboard />} />
    {/* ... */}
  </Routes>
</Suspense>
```

### Virtualized Search Results

```typescript
// components/search/SearchResults.tsx
import { FixedSizeList as List } from 'react-window';

const SearchResults: React.FC<{ results: SearchResult[] }> = ({ results }) => (
  <List
    height={600}
    itemCount={results.length}
    itemSize={120}
    width="100%"
  >
    {({ index, style }) => (
      <div style={style}>
        <ResultCard result={results[index]} />
      </div>
    )}
  </List>
);
```

### Graph Rendering Performance

- Use **canvas-based rendering** (`react-force-graph-2d`) for 5000+ nodes — avoids SVG DOM overhead
- **Virtual nodes** for high-degree hubs: cluster nodes with > 100 connections
- **Progressive loading**: initially render only nodes within 2 hops of selected entity;
  expand on demand
- **Web Worker** for force simulation: keeps main thread free for UI interactions

```typescript
// workers/graphLayout.worker.ts
import * as d3 from 'd3-force';

self.onmessage = ({ data }) => {
  if (data.type === 'layout') {
    const simulation = d3.forceSimulation(data.graph.nodes)
      .force('link', d3.forceLink(data.graph.edges).id((d: any) => d.id))
      .force('charge', d3.forceManyBody().strength(-100))
      .force('center', d3.forceCenter(0, 0))
      .stop();

    // Run 300 ticks synchronously in the worker
    simulation.tick(300);

    self.postMessage({ type: 'layout_done', nodes: data.graph.nodes, edges: data.graph.edges });
  }
};
```

---

## 9. Vite Configuration

```typescript
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    target: 'es2020',
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          mui: ['@mui/material', '@mui/x-data-grid'],
          graph: ['react-force-graph-2d', 'cytoscape'],
          redux: ['@reduxjs/toolkit', 'react-redux'],
        },
      },
    },
  },
  worker: {
    format: 'es',
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
});
```
