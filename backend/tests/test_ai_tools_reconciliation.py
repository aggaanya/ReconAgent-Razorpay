"""Reconciliation Finance Tool tests.

Pins the tool boundary: registry integration, input validation, session
policy (no DB needed here — unlike every database-backed tool), JSON-safe
envelopes, determinism through the tool, and planner-catalog visibility.
No LLM, no database, no paid calls.
"""

import json

import pytest

from app.ai.tools import (
    ALL_FINANCE_TOOLS,
    FINANCE_TOOLS,
    RECONCILE_TRANSACTION_TOOL,
    REVENUE_TOOL,
    FinanceToolError,
    InvalidToolInputError,
    get_finance_tool,
)
from app.ai.tools.reconciliation import ReconcileToolInput


class TestRegistryIntegration:
    def test_tool_is_registered_under_stable_name(self):
        assert RECONCILE_TRANSACTION_TOOL.name == "reconcile_transactions"
        assert FINANCE_TOOLS["reconcile_transactions"] is (
            RECONCILE_TRANSACTION_TOOL
        )
        assert RECONCILE_TRANSACTION_TOOL in ALL_FINANCE_TOOLS

    def test_lookup_by_name(self):
        assert get_finance_tool("reconcile_transactions") is (
            RECONCILE_TRANSACTION_TOOL
        )


class TestSessionPolicy:
    def test_reconciliation_needs_no_database_session(self):
        envelope = RECONCILE_TRANSACTION_TOOL.run(
            None, {"source": "synthetic", "seed": 42, "size": 50}
        )
        assert envelope["tool"] == "reconcile_transactions"
        assert envelope["data"]["total_records"] == 50

    def test_database_backed_tools_still_require_a_session(self):
        with pytest.raises(InvalidToolInputError, match="session"):
            REVENUE_TOOL.run(None, {})


class TestInputValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"size": 10},  # below the Track-04 floor
            {"size": 5001},  # above the cap
            {"seed": -1},
            {"source": "live_database"},  # not an allowlisted source
            {"records": "inject-me"},  # record injection is impossible
        ],
    )
    def test_invalid_inputs_are_rejected(self, payload):
        with pytest.raises(FinanceToolError) as excinfo:
            RECONCILE_TRANSACTION_TOOL.run(None, payload)
        assert excinfo.value.code == "invalid_input"

    def test_defaults_match_the_track_04_batch(self):
        params = ReconcileToolInput()
        assert params.source == "synthetic"
        assert params.seed == 42
        assert params.size == 100


class TestEnvelopeContract:
    def test_envelope_is_json_safe_and_fully_populated(self):
        envelope = RECONCILE_TRANSACTION_TOOL.run(None, {"size": 50})
        encoded = json.loads(json.dumps(envelope))  # round trip
        assert encoded["tool"] == "reconcile_transactions"
        summary = encoded["data"]
        for key in (
            "total_records",
            "matched_count",
            "exception_count",
            "unresolved_count",
            "match_rate",
            "accuracy",
            "processing_time_ms",
            "throughput_records_per_second",
            "exception_breakdown",
        ):
            assert key in summary
        assert len(encoded["exceptions"]) == summary["exception_count"]
        assert len(encoded["results"]) == summary["total_records"]

    def test_exception_entries_carry_required_fields(self):
        envelope = RECONCILE_TRANSACTION_TOOL.run(None, {"size": 50})
        first = envelope["exceptions"][0]
        for key in (
            "source_transaction_id",
            "matched_transaction_id",
            "status",
            "expected_amount_minor",
            "actual_amount_minor",
            "difference_minor",
            "currency",
            "reason",
            "exception_type",
        ):
            assert key in first
        assert first["exception_type"] == first["status"]

    def test_no_ground_truth_ever_leaks_into_the_envelope(self):
        envelope = RECONCILE_TRANSACTION_TOOL.run(None, {"size": 50})
        encoded = json.dumps(envelope)
        assert "expected_status" not in encoded
        assert "ground_truth" not in encoded
        assert "case_id" not in encoded


class TestDeterminismThroughTool:
    def test_identical_arguments_produce_identical_metrics(self):
        first = RECONCILE_TRANSACTION_TOOL.run(None, {"seed": 42, "size": 60})
        second = RECONCILE_TRANSACTION_TOOL.run(None, {"seed": 42, "size": 60})
        # Timing fields are the only legitimate run-to-run variance.
        for envelope in (first, second):
            envelope["data"].pop("processing_time_ms")
            envelope["data"].pop("throughput_records_per_second")
        assert first["data"] == second["data"]
        assert first["exceptions"] == second["exceptions"]

    def test_different_seed_changes_the_case_mix_placement(self):
        a = RECONCILE_TRANSACTION_TOOL.run(None, {"seed": 1, "size": 50})
        b = RECONCILE_TRANSACTION_TOOL.run(None, {"seed": 2, "size": 50})
        a_sources = {r["source_transaction_id"] for r in a["results"]}
        b_sources = {r["source_transaction_id"] for r in b["results"]}
        assert a_sources != b_sources


class TestPlannerVisibility:
    def test_catalog_exposes_the_tool_with_argument_schema(self):
        from app.ai.graph.prompts import tool_catalog

        catalog = {entry["name"]: entry for entry in tool_catalog()}
        entry = catalog["reconcile_transactions"]
        # Compact catalog format: "arguments" contains required + key optional fields.
        args = entry["arguments"]
        assert "size" in args
        assert "source" in args
