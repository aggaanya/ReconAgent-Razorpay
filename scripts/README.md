# scripts/

Automation scripts for local development and operations.

**Available:**

- `reconcile_benchmark.py` — Track 04 reconciliation evaluation harness.
  Runs the deterministic engine over a seeded synthetic batch (default
  seed 42, size 100) and prints a RECONCILIATION EVALUATION block:
  records processed, matched/exceptions, match rate, measured accuracy
  vs ground truth, processing time, throughput, and the exception
  breakdown. Exits nonzero on any ground-truth mismatch. Run with the
  backend venv interpreter from anywhere; it bootstraps `backend/` onto
  `sys.path` itself.

    backend/.venv/Scripts/python.exe scripts/reconcile_benchmark.py [--seed 42] [--size 100]

**Planned contents (added as they become executable, not before):**
- local environment bootstrap (venv creation, dependency install)
- database setup helpers
- synthetic data generation runner for the finance API demo dataset
- dev-server launch helpers
