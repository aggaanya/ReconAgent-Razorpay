# tests/

Cross-cutting and end-to-end test suites that span services.

**Convention:**
- Backend unit/API tests live in `backend/tests/` (pytest)
- Frontend component tests are co-located with the app under `frontend/src` (Vitest)
- This directory holds repository-level E2E tests once the system has multiple working phases to exercise (master spec §30)

Currently empty by design — there is no cross-service behavior to test yet.
