"""Track 04 reconciliation evaluation harness.

Runs the deterministic reconciliation engine over a seeded synthetic
batch and prints a RECONCILIATION EVALUATION block: volume metrics,
match rate, throughput, processing time, the exception breakdown, and —
unlike any serving path — the measured accuracy against ground truth.

Ground truth exists ONLY here (and in tests): the engine never sees it.
A nonzero mismatch count or sub-100% accuracy means either the engine or
the dataset drifted from docs/RECONCILIATION_ARCHITECTURE.md, so the
script exits nonzero to fail CI loudly.

Usage (from the repo root):
    .venv/python via backend venv:
    backend/.venv/Scripts/python.exe scripts/reconcile_benchmark.py \
        [--seed 42] [--size 100] [--json]

``--json`` prints the same machine-readable evaluation payload served by
``GET /api/v1/ai/reconcile/evaluation`` (shared builder
``app.services.reconciliation.evaluate_batch_with_ground_truth``).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make the backend package importable when running from the repo root.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.reconciliation import (  # noqa: E402
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    ReconciliationStatus,
)
from app.services.reconciliation import (  # noqa: E402
    build_report,
    evaluate_batch_with_ground_truth,
    evaluate_ground_truth,
)
from app.services.reconciliation_synthetic import (  # noqa: E402
    generate_synthetic_batch,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the Track 04 reconciliation engine against a "
            "seeded synthetic batch with ground truth."
        )
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="generator seed (default: 42)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=100,
        help="number of cases in the batch (default: 100)",
    )
    parser.add_argument(
        "--max-settlement-delay-days",
        type=int,
        default=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        help=(
            "settlement-delay tolerance in days "
            f"(default: {DEFAULT_MAX_SETTLEMENT_DELAY_DAYS})"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "print a machine-readable evaluation payload (identical to "
            "GET /api/v1/ai/reconcile/evaluation) instead of the text block"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.json:
        payload = evaluate_batch_with_ground_truth(
            seed=args.seed,
            size=args.size,
            max_settlement_delay_days=args.max_settlement_delay_days,
        )
        print(payload.model_dump_json(indent=2))
        ok = payload.accuracy == 100.0 and not payload.mismatches
        print("RESULT               : PASS" if ok else "RESULT               : FAIL")
        return 0 if ok else 1

    started = time.perf_counter()
    batch = generate_synthetic_batch(seed=args.seed, size=args.size)
    generated = time.perf_counter()

    report = build_report(
        batch.payments,
        batch.settlements,
        batch.refunds,
        max_settlement_delay_days=args.max_settlement_delay_days,
    )
    finished = time.perf_counter()

    evaluation = evaluate_ground_truth(report.results, batch.ground_truth)
    summary = report.summary

    engine_seconds = max(finished - generated, 1e-9)
    throughput = summary.total_records / engine_seconds

    def pct(value: float | None) -> str:
        return f"{value:.2f}%" if value is not None else "n/a"

    print("=" * 64)
    print("RECONCILIATION EVALUATION")
    print("=" * 64)
    print(f"Dataset              : synthetic seed={args.seed} size={args.size}")
    print(f"Records processed    : {summary.total_records}")
    print(f"Matched              : {summary.matched_count}")
    print(f"Exceptions           : {summary.exception_count}")
    print(f"Match rate           : {pct(summary.match_rate)}")
    print(f"Accuracy (vs truth)  : {pct(evaluation.accuracy)}")
    print(f"False positives      : {evaluation.false_positives}")
    print(f"False negatives      : {evaluation.false_negatives}")
    print(f"Precision            : {pct(evaluation.precision)}")
    print(f"Recall               : {pct(evaluation.recall)}")
    print(f"F1                   : {pct(evaluation.f1)}")
    print(
        f"Processing time      : {engine_seconds * 1000:.3f} ms "
        f"(report timer: {summary.processing_time_ms:.3f} ms)"
    )
    print(f"Throughput           : {throughput:,.2f} records/sec")
    print("-" * 64)
    print("Exception breakdown:")
    for status in ReconciliationStatus:
        count = summary.exception_breakdown.get(status.value, 0)
        print(f"  {status.value:<36}: {count}")
    print("-" * 64)
    print(f"Mismatches vs ground truth: {len(evaluation.mismatches)}")
    for mismatch in evaluation.mismatches:
        print(
            f"  case={mismatch['case_id']} kind={mismatch['kind']} "
            f"expected={mismatch['expected']} actual={mismatch['actual']}"
        )
    print("=" * 64)

    ok = evaluation.accuracy == 100.0 and not evaluation.mismatches
    print("RESULT               : PASS" if ok else "RESULT               : FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
