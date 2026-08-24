"""Evaluation surface tests: GET /api/v1/ai/reconcile/evaluation + CLI --json.

Pins the judge-visible measured-quality contract:
- the evaluation endpoint returns exact canonical metrics measured against
  the isolated ground-truth evaluator (accuracy/precision/recall/F1/TP/FP/FN);
- the payload is aggregate-only — never raw ground-truth records;
- the serving endpoint keeps ``accuracy=null`` while this explicitly
  marked evaluation surface measures it;
- responses are deterministic modulo timing;
- the benchmark CLI ``--json`` payload matches the endpoint contract.

Zero credentials required anywhere: no DB, no LLM, no Razorpay.
"""

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.reconciliation import ReconciliationStatus
from app.services.reconciliation_synthetic import CASE_DISTRIBUTION

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "reconcile_benchmark.py"
)

EXPECTED_BREAKDOWN = {
    status.value: count
    for status, count in CASE_DISTRIBUTION.items()
    if status is not ReconciliationStatus.MATCHED
}

EXPECTED_KEYS = {
    "surface",
    "dataset",
    "total_records",
    "matched_count",
    "exception_count",
    "unresolved_count",
    "match_rate",
    "accuracy",
    "precision",
    "recall",
    "f1",
    "true_positives",
    "false_positives",
    "false_negatives",
    "throughput_records_per_second",
    "exception_breakdown",
    "mismatches",
}


def get_evaluation(client: TestClient) -> dict:
    response = client.get("/api/v1/ai/reconcile/evaluation")
    assert response.status_code == 200
    return response.json()


class TestEvaluationEndpoint:
    def test_canonical_batch_measures_exact_quality(self, client) -> None:
        body = get_evaluation(client)

        assert body["surface"] == "evaluation"
        assert body["dataset"] == {"source": "synthetic", "seed": 42, "size": 100}
        assert body["total_records"] == 100
        assert body["matched_count"] == 58
        assert body["exception_count"] == 42
        assert body["unresolved_count"] == 2
        assert body["match_rate"] == 58.0
        # Measured quality — the whole point of this surface.
        assert body["accuracy"] == 100.0
        assert body["precision"] == 100.0
        assert body["recall"] == 100.0
        assert body["f1"] == 100.0
        assert body["true_positives"] == 42
        assert body["false_positives"] == 0
        assert body["false_negatives"] == 0
        assert body["throughput_records_per_second"] > 0
        assert body["exception_breakdown"] == EXPECTED_BREAKDOWN
        assert body["mismatches"] == []

    def test_payload_is_aggregate_only_never_truth_records(self, client) -> None:
        response = client.get("/api/v1/ai/reconcile/evaluation")
        assert set(response.json().keys()) == EXPECTED_KEYS
        raw = response.text
        for marker in (
            "expected_status",
            "expected_secondary_issues",
            "expected_reason_fragment",
            "GroundTruthEntry",
            "payment_id",
            "settlement_id",
        ):
            assert marker not in raw

    def test_serving_accuracy_stays_null_while_evaluation_measures(
        self, client
    ) -> None:
        served = client.post(
            "/api/v1/ai/reconcile",
            json={"source": "synthetic", "seed": 42, "size": 100,
                  "explain": False},
        )
        assert served.status_code == 200
        assert served.json()["accuracy"] is None

        evaluated = get_evaluation(client)
        assert evaluated["accuracy"] == 100.0

    def test_deterministic_modulo_timing(self, client) -> None:
        first = get_evaluation(client)
        second = get_evaluation(client)
        volatile = {"throughput_records_per_second"}
        assert {k: v for k, v in first.items() if k not in volatile} == {
            k: v for k, v in second.items() if k not in volatile
        }

    def test_custom_seed_relocates_cases_without_changing_counts(
        self, client
    ) -> None:
        response = client.get("/api/v1/ai/reconcile/evaluation?seed=7")
        assert response.status_code == 200
        body = response.json()
        assert body["dataset"]["seed"] == 7
        assert body["matched_count"] == 58
        assert body["exception_count"] == 42

    def test_out_of_bounds_sizes_rejected_without_running(self, client) -> None:
        assert (
            client.get("/api/v1/ai/reconcile/evaluation?size=49").status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/ai/reconcile/evaluation?size=5001"
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v1/ai/reconcile/evaluation?seed=-1").status_code
            == 422
        )


class TestBenchmarkJsonContract:
    def test_cli_json_matches_endpoint_and_passes(self, client) -> None:
        endpoint_body = get_evaluation(client)

        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert "RESULT               : PASS" in completed.stdout

        lines = completed.stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line == "{")
        end = next(i for i, line in enumerate(lines) if line == "}")
        payload = json.loads("\n".join(lines[start : end + 1]))

        assert set(payload.keys()) == EXPECTED_KEYS
        assert payload["surface"] == "evaluation"
        assert payload["accuracy"] == 100.0
        assert payload["true_positives"] == endpoint_body["true_positives"]
        assert payload["false_positives"] == 0
        assert payload["false_negatives"] == 0
        assert payload["exception_breakdown"] == EXPECTED_BREAKDOWN
