# Bot 2 — Review: Phase 5 — React Frontend

## Your Role

You are reviewing the React frontend for auth security, UX correctness, NER color mapping,
RTK Query wiring, and spec compliance.

---

## Reference Documents

- `specifications/09-frontend-design.md` — component specs, routing, state management
- `specifications/10-auth-security.md` — JWT storage, cookie security
- `tasks/LESSONS.md` — localStorage token persistence, canonical NER labels, RTK reauth fix

---

## Review Checklist

### A. Auth Token Storage (CRITICAL)

- [ ] `authSlice.ts` persists `kg_access_token` to `localStorage` on `setCredentials`
- [ ] `authSlice.ts` persists `kg_access_token` to `localStorage` on `setAccessToken`
- [ ] Initial state restores `accessToken` from `localStorage.getItem('kg_access_token')`
- [ ] Logout clears both `kg_user` and `kg_access_token` from localStorage
- [ ] Token is NOT stored in `sessionStorage` (which clears on tab close)

### B. RTK Reauth Logic (BLOCKER if missing)

- [ ] After successful `/auth/refresh` call:
  - If `state.auth.user` is non-null → dispatch `setCredentials({ accessToken, user })`
  - If `state.auth.user` is null (page reload, user not yet in Redux) → dispatch `setAccessToken(accessToken)`
  - BOTH branches must be handled — missing the `else` branch causes silent token non-storage
- [ ] Original request is retried after successful refresh
- [ ] Failed refresh dispatches `clearCredentials()` and does NOT retry

### C. NER Color Map (BLOCKER if wrong)

- [ ] `ENTITY_TYPE_COLORS` in `ForceGraph.tsx` uses canonical keys: `ORGANIZATION`, `LOCATION`, `PERSON`
- [ ] NO entries for spaCy shorthand: `ORG`, `GPE`, `LOC`, `NORP`, `FAC`
- [ ] Color map is used in `nodeCanvasObject` (canvas rendering), not in SVG attributes

### D. Graph Viewer

- [ ] `react-force-graph-2d` used (canvas), NOT `react-force-graph` (SVG variant)
- [ ] `nodeCanvasObject` prop defined when `showLabels=true`
- [ ] Label rendering uses `ctx.fillText` (canvas API, not innerHTML)
- [ ] Node color from `ENTITY_TYPE_COLORS[node.entity_type]` with fallback `'#999'`
- [ ] `showLabels` state toggles label rendering (LabelIcon/LabelOffIcon in toolbar)
- [ ] Graph layout runs in Web Worker (`graphLayout.worker.ts`), not on main thread

### E. Collections and Ingest

- [ ] Collections DataGrid columns: name, doc_count, status, created_at
- [ ] IngestPanel shows job progress: progress bar + current file name
- [ ] `POST /ingest/folder` body includes `collection_id` AND `folder_path`

### F. Search

- [ ] 4 search modes: hybrid, vector, keyword, graph (as tabs or segmented control)
- [ ] Topic filter from `TopicSidebar` included in search request body
- [ ] `ResultCard` shows: text excerpt, doc title, page number, relevance score

### G. Code Splitting

- [ ] All page components wrapped in `React.lazy()`
- [ ] `Suspense` boundary with fallback wraps lazy components
- [ ] No large libraries (MUI, D3, force-graph) imported in critical path (check Vite bundle)

### H. WebSocket

- [ ] WebSocket middleware connects to `WS /api/v1/ws`
- [ ] `graph_update` message type triggers RTK invalidateTags(['Graph', 'Node'])
- [ ] `job_progress` message type updates job status in Redux

---

## Output Format

```
[SEVERITY] File: src/path/file.tsx:line
Description:
Spec reference:
Fix:
  // TypeScript correction
```

---

## Common Mistakes

1. **Missing else in reauth**: `if (state.auth.user) { setCredentials(...) }` without `else { setAccessToken(...) }` — silent bug on page reload where `user` is null until restore from localStorage. BLOCKER.
2. **ORG in ENTITY_TYPE_COLORS**: Mapping `ORG: '#2196F3'` instead of `ORGANIZATION: '#2196F3'` — nodes never get colored because stored type is ORGANIZATION. BLOCKER.
3. **sessionStorage instead of localStorage**: Token cleared on tab close, causing frequent auth loops.
4. **SVG graph**: Using `react-force-graph` (SVG) instead of `react-force-graph-2d` (canvas) — SVG crashes with 5000+ nodes.
5. **Missing RequireAuth**: Routes to /dashboard, /collection/:id, /graph/:id without RequireAuth wrapper — unauthenticated users see app pages.
