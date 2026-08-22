# infra/

Deployment and infrastructure artifacts.

**Deliberately minimal.** Per the master spec (§10.1), Docker/containerization is optional for this project, and production deployment concerns (gateways, Kubernetes, message queues, secrets managers) belong to later hardening phases. Artifacts appear here only when actually required — no speculative infrastructure.

Nothing is needed here yet: Phase 1 runs backend (uvicorn) and frontend (Vite dev server) directly on the host.
