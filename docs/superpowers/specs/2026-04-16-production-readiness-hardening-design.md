# Production Readiness & Hardening Design

> **Goal:** Fix all BLOCKER/HIGH security, accessibility, UX, and testing gaps identified in the comprehensive audit to achieve production-readiness.

## Audit Findings Summary

### Security (3 BLOCKER, 5 HIGH, 18 MEDIUM)

| ID | Severity | Issue | Location |
|---|---|---|---|
| AUTH-1 | BLOCKER | Dev token bypass — trivially forgeable when RSA keys absent | `python-api/app/auth/jwt.py:28-31,65-74` |
| SEC-1 | BLOCKER | Real Google OAuth secret committed to `.env` | `.env:23-24` |
| INJ-1 | BLOCKER | Unsanitized string interpolation in LanceDB WHERE clauses (single-quote bypass) | `python-api/app/db/lancedb_client.py:977` |
| INJ-2 | HIGH | Unsanitized `doc_id`/`cid` in graph.py WHERE clauses | `python-api/app/routers/graph.py:142,274,344` |
| MISC-1 | HIGH | Drive webhook endpoint has no authentication | `python-api/app/routers/drive.py:151` |
| INFRA-2 | HIGH | Docker containers run as root | `docker/Dockerfile.api`, `docker/Dockerfile.frontend` |
| INJ-4 | HIGH | `FeedDocumentsRequest.file_paths` not validated against allowed roots | `python-api/app/routers/ingest.py:79-119` |
| INJ-6 | HIGH | No limit on search `collection_ids` count (DoS vector) | `python-api/app/routers/search.py:105` |
| DP-1 | MEDIUM | Internal error details leaked in HTTP responses | Multiple routers |
| AUTH-2 | MEDIUM | In-memory revoked token set not shared across workers | `python-api/app/auth/jwt.py:15` |
| AUTH-5 | MEDIUM | JWT in WebSocket URL query param | `python-api/app/routers/ws.py` |
| INJ-5 | MEDIUM | No validation on collection name (XSS risk) | `python-api/app/routers/collections.py` |
| INJ-7 | MEDIUM | Auth endpoints exempt from rate limiting | `python-api/app/auth/middleware.py:31` |
| DP-2 | MEDIUM | No encryption at rest for LanceDB data | Architecture |
| DP-3 | MEDIUM | Drive access tokens stored in plaintext | `python-api/app/db/lancedb_client.py:77` |
| FE-2 | MEDIUM | User object persisted in localStorage | `frontend/src/store/slices/authSlice.ts` |
| INFRA-1 | MEDIUM | API port 8000 exposed directly bypassing nginx | `docker-compose.yml:28` |
| INFRA-4 | MEDIUM | Nginx lacks security headers for static assets | `docker/nginx.conf` |
| SEC-3 | MEDIUM | Dev docker-compose has credential placeholders as defaults | `docker/docker-compose.dev.yml` |
| DEP-1 | MEDIUM | Python deps use `>=` version pinning | `python-api/requirements.txt` |
| MISC-5 | MEDIUM | Finetune endpoint accessible by any user (cost risk) | `python-api/app/routers/finetune.py` |
| MISC-6 | MEDIUM | First user auto-granted admin role (race condition) | `python-api/app/db/lancedb_client.py:245-246` |

### UI/UX (8 CRITICAL/HIGH, 22 MEDIUM)

| ID | Severity | Issue | Location |
|---|---|---|---|
| A11Y-1 | CRITICAL | ForceGraph canvas completely inaccessible — no ARIA, no keyboard nav | `frontend/src/components/graph/ForceGraph.tsx` |
| A11Y-2 | HIGH | 15+ interactive elements missing `aria-label` | NavBar, Dashboard, GraphControls, ResultCard, NodeDetailPanel, Collection, AgentQuery, FineTune |
| A11Y-3 | HIGH | "clear" text in GraphControls styled as control but uses `<Typography>` not `<button>` | `frontend/src/components/graph/GraphControls.tsx:134-138` |
| UX-1 | HIGH | Document deletion (Collection.tsx) has no confirmation dialog | `frontend/src/pages/Collection.tsx:194-197` |
| UX-2 | HIGH | Color contrast failures in ENTITY_TYPE_COLORS (PERCENT, MONEY, DATE) | `frontend/src/utils/entityColors.ts` |
| UX-3 | HIGH | Fixed-width overlays break on mobile (210px, 260px, 380px, 320px) | GraphControls, NerKeywordPanel, NodeDetailPanel, PathFinder |
| UX-4 | HIGH | Layout drawer is not responsive — fixed 220px width | `frontend/src/components/common/Layout.tsx:23` |
| UX-5 | HIGH | Search has no pagination — hard limit of 50 results | `frontend/src/pages/Search.tsx:38` |
| UX-6 | HIGH | Date range inputs in GraphViewer use raw `<input>` with no label | `frontend/src/pages/GraphViewer.tsx:497-507` |
| UX-7 | HIGH | Dead `cytoscape` dependency in bundle | `frontend/vite.config.ts:21` |
| UX-8 | MEDIUM | Auth refresh failure does not redirect to login | `frontend/src/api/baseApi.ts:46` |
| UX-9 | MEDIUM | Search mutation not debounced on Search page | `frontend/src/pages/Search.tsx:29-43` |
| UX-10 | MEDIUM | No global error interceptor for unhandled RTK errors | Architecture |
| UX-11 | MEDIUM | WebSocket reconnects indefinitely with no max/UX notification | `frontend/src/store/wsMiddleware.ts` |
| UX-12 | MEDIUM | No graph export button despite API endpoint existing | `frontend/src/pages/GraphViewer.tsx` |
| UX-13 | MEDIUM | Snackbar is singleton — rapid errors overwrite | `frontend/src/components/common/Layout.tsx` |
| UX-14 | MEDIUM | No confirmation before fine-tuning job (cost risk) | `frontend/src/pages/FineTune.tsx` |

### Testing (major gaps)

| Gap | Severity | Details |
|-----|----------|---------|
| `admin.py` router | HIGH | Zero tests for admin endpoints |
| `topics.py` router | HIGH | Zero tests for topic endpoints |
| Rust `ontology/rules.rs` | HIGH | Zero unit tests for validation rules |
| Rust `ingestion/extractor.rs` | HIGH | Zero tests for text extraction |
| 8/10 page components | HIGH | No tests: LoginPage, CallbackPage, Collection, OntologyEditor, Settings, FineTune, AgentQuery, GraphViewer |
| 11/20 components | MEDIUM | No tests: IngestPanel, NodeDetailPanel, PathFinder, etc. |
| No E2E CI | HIGH | Playwright tests never run in CI |
| No security test suite | HIGH | No dedicated tests for injection, auth bypass |
| No coverage reporting | MEDIUM | No external coverage tracking |

---

## Design Decisions

### Architecture

Six parallel workstreams working simultaneously, each internally ordered by severity (BLOCKER → HIGH → MEDIUM). Each stream follows strict TDD: write failing test first, then fix.

1. **Security Fixes** — Backend code changes for BLOCKER/HIGH security issues
2. **Security Tests** — New security-focused test suite (injection, auth bypass, rate limits)
3. **UI/UX Fixes** — Frontend code changes for CRITICAL/HIGH a11y + UX issues
4. **UI/UX Tests** — Tests for untested components and pages
5. **Backend Test Gaps** — Cover untested routers and Rust modules
6. **CI/CD Hardening** — E2E CI, security scanning, coverage reporting

### Principles

- **TDD for every change**: Write failing test → verify failure → implement fix → verify pass → commit
- **Minimal changes per commit**: One issue or one test file per commit
- **No scope creep**: Only fix issues identified in the audit
- **Backward compatibility**: All changes must pass existing tests

### Cross-stream Dependencies

- Stream 1 (Security Fixes) should be merged before Stream 2 (Security Tests) finalizes, so tests verify actual fixes
- Stream 3 (UI/UX Fixes) can proceed independently — fixes don't break existing tests
- Stream 4 (UI/UX Tests) depends on Stream 3 being partially complete for better test targets
- Stream 5 (Backend Test Gaps) is fully independent
- Stream 6 (CI/CD) should be last to ensure all new tests work in CI

### Security Fix Approach

1. **Dev token bypass**: Gate behind explicit `DEV_MODE=true` env var (default `false`), crash on missing RSA keys in production
2. **LanceDB injection**: Add `_param_id()` and `_param_str()` parameterized helpers that use LanceDB's `.where()` with properly escaped values; enforce `_safe_id()` on ALL query parameters
3. **Drive webhook**: Add `X-Goog-Channel-Token` verification (Google supports registering a verification token per channel)
4. **Docker root**: Add `USER` directive with non-root user (UID 1000)
5. **FeedDocumentsRequest**: Validate each path against `ALLOWED_FOLDER_ROOTS`
6. **Search collection_ids**: Limit to max 10

### UI/UX Fix Approach

1. **ForceGraph a11y**: Add `role="img"` + `aria-label` to canvas container; add keyboard instructions overlay
2. **aria-labels**: Systematic addition to all 15+ missing elements
3. **"clear" text → button**: Change `<Typography onClick>` to `<Button size="small">` or `<IconButton>`
4. **Delete confirmation**: Add MUI `<Dialog>` for document deletion
5. **Color contrast**: Adjust ENTITY_TYPE_COLORS to meet WCAG AA (4.5:1 ratio)
6. **Responsive overlays**: Use MUI `sx={{ width: { xs: '90vw', sm: 210 } }}` breakpoints
7. **Responsive drawer**: Use MUI `<Drawer variant="temporary">` on mobile
8. **Search pagination**: Add "Load more" button + offset parameter
9. **Dead cytoscape**: Remove from vite.config.ts and package.json

### Test Gap Approach

1. **Admin router tests**: New `test_admin.py` covering all 6 endpoints
2. **Topics router tests**: New `test_topics.py` covering both endpoints
3. **Rust ontology tests**: New `ontology/rules_test.rs` and `ontology/types_test.rs`
4. **Rust extractor tests**: New `ingestion/extractor_test.rs`
5. **Frontend page tests**: New test files for 8 untested pages
6. **Frontend component tests**: New test files for 11 untested components
7. **Security test suite**: New `test_security.py` with injection, auth bypass, CSRF tests
8. **E2E CI**: New `.github/workflows/e2e-ci.yml`