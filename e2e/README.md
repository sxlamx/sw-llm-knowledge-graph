# E2E Tests — Playwright

End-to-end tests for the Knowledge Graph Builder frontend.

## Prerequisites

```bash
cd e2e
npm install
npx playwright install chromium
```

## Running

```bash
# Against local dev server (default: http://localhost:5333)
npm test

# Against a custom deployment
E2E_BASE_URL=https://staging.example.com npm test

# Headed mode (watch the browser)
npm run test:headed
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:5333` | Frontend URL |
| `E2E_API_BASE` | `http://localhost:8333/api/v1` | API URL (for route mocking reference) |
| `E2E_DEV_TOKEN` | `dev_token_e2e` | Dev JWT injected into Redux store |

## Test files

| File | Coverage |
|---|---|
| `auth.spec.ts` | Landing page, login redirect, logout |
| `collections.spec.ts` | Dashboard, create dialog, dark mode toggle |
| `ingest.spec.ts` | Collection page, ingest panel, document list |
| `search.spec.ts` | Search bar, result cards, score display |
| `graph.spec.ts` | Graph viewer, collection selector, analytics toggle |
| `collab.spec.ts` | WebSocket collab room, presence join/leave indicators |

## Auth injection

Tests use `loginWithDevToken()` which writes a `kg_user` object to `localStorage` before the app bootstraps, bypassing Google OAuth entirely. The backend must run with `GOOGLE_CLIENT_ID=""` (dev mode) so that dev tokens are accepted.
