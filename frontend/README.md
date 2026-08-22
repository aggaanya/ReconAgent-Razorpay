# ReconAgent Frontend

React single-page app for ReconAgent.

**Status: Phase 1 shell implemented.** Minimal enterprise-style app shell that proves frontend → backend connectivity: it renders "ReconAgent" with a live **Backend Status** pill (Connected / Unavailable / Checking) driven by a real request to `GET /health`.

## Stack

- React 19 + Vite 8 (JavaScript/JSX)
- Tailwind CSS 4 (`@tailwindcss/vite` plugin)
- Recharts 3 (installed for upcoming dashboard phases; unused in Phase 1 UI)

## Layout

```
frontend/
├── src/
│   ├── api/client.js               # API abstraction: env base URL, 10s timeout, JSON errors
│   ├── hooks/useBackendHealth.js   # status state machine + retry
│   ├── pages/SystemStatusPage.jsx  # app shell page
│   ├── components/StatusPill.jsx   # status badge
│   └── App.jsx / main.jsx / index.css
├── .env.example                    # VITE_API_BASE_URL template
└── vite.config.js
```

## Conventions

- Backend base URL comes only from `VITE_API_BASE_URL` (see `.env.example`) — never hardcoded.
- All API calls go through `src/api/client.js`; components never call `fetch()` directly.
- Backend unavailability degrades gracefully (status pill + alert box; no stack traces in the UI).
- Automated frontend tests are intentionally not set up yet (single-view shell); Vitest + Testing Library will be added when UI logic grows.

## Run

```powershell
npm install
npm run dev        # dev server on http://localhost:5173
npm run build      # production build to dist/
npm run preview    # serve the production build
```
