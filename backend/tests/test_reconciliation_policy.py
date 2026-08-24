"""Triage policy tests: deterministic severity, priority, recommended actions.

The policy layer (app.services.reconciliation_policy) is pure: no LLM, no
database, no provider access, no financial arithmetic. These tests pin:

- every exception type maps to exactly one documented severity and action;
- the bounded priority scale is the documented constant table;
- MATCHED carries INFO/10 but never a recommended action;
- UNRESOLVED follows existing project semantics (human review required);
- annotation is byte-deterministic and never mutates engine output fields;
- the shared Severity vocabulary from app.schemas.signals is reused (a
  second conflicting taxonomy must not exist);
- end-to-end: build_report results and the served API payload carry the
  annotations, while MATCHED rows stay out of the exceptions list.

Zero credentials required anywhere: no DB, no LLM, no Razorpay.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.reconciliation import (
    EXCEPTION_STATUSES,
    ReconciliationStatus,
)
from app.schemas.signals import FinancialSignal, Severity
from app.services import reconciliation_policy as policy
from app.services.reconciliation import build_report
from app.services.reconciliation_synthetic import generate_synthetic_batch


@pytest.fixture
def reconcile_client(monkeypatch):
    """TestClient with a pristine AI-agent singleton and no env config.

    Mirrors the fixture in test_api_ai_reconcile.py: the synthetic-source
    reconcile endpoint never touches the database or an LLM.
    """
    import app.api.ai as ai_module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    saved = ai_module._agent
    ai_module._agent = None
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    ai_module.reset_ai_agent()
    ai_module._agent = saved
    get_settings.cache_clear()

EXPECTED_SEVERITY = {
    ReconciliationStatus.DUPLICATE_SETTLEMENT: "CRITICAL",
    ReconciliationStatus.REFUND_MISMATCH: "CRITICAL",
    ReconciliationStatus.MISSING_SETTLEMENT: "HIGH",
    ReconciliationStatus.MISSING_PAYMENT: "HIGH",
    ReconciliationStatus.CURRENCY_MISMATCH: "HIGH",
    ReconciliationStatus.AMOUNT_MISMATCH: "HIGH",
    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE: "HIGH",
    ReconciliationStatus.UNRESOLVED: "HIGH",
    ReconciliationStatus.INVALID_STATUS: "MEDIUM",
    ReconciliationStatus.SETTLEMENT_DELAY: "LOW",
}

EXPECTED_PRIORITY = {
    "CRITICAL": 100,
    "HIGH": 80,
    "MEDIUM": 60,
    "LOW": 30,
    "INFO": 10,
}


def test_every_exception_type_has_documented_severity() -> None:
    assert set(EXCEPTION_STATUSES) == set(EXPECTED_SEVERITY)
    for status in EXCEPTION_STATUSES:
        assert policy.severity_for(status) == EXPECTED_SEVERITY[status]
        assert (
            policy.SEVERITY_BY_EXCEPTION[status] == EXPECTED_SEVERITY[status]
        )


def test_every_exception_type_has_operator_action() -> None:
    for status in EXCEPTION_STATUSES:
        action = policy.recommended_action_for(status)
        assert isinstance(action, str) and action.strip()
        # Guidance says what to investigate next; it never claims work
        # was already performed or invents financial facts.
        lowered = action.lower()
        assert "resolved" not in lowered.replace("unresolved", "")
        assert not any(
            token in lowered
            for token in ("password", "api key", "secret", "token")
        )


def test_policy_tables_are_complete() -> None:
    assert set(policy.SEVERITY_BY_EXCEPTION) == set(EXCEPTION_STATUSES)
    assert set(policy.ACTION_BY_EXCEPTION) == set(EXCEPTION_STATUSES)


def test_priority_scale_is_documented_constants() -> None:
    assert policy.PRIORITY_BY_SEVERITY == EXPECTED_PRIORITY
    for status in EXCEPTION_STATUSES:
        severity = policy.severity_for(status)
        assert policy.priority_for(status) == EXPECTED_PRIORITY[severity]


@pytest.mark.parametrize(
    ("status", "severity", "priority"),
    [
        (ReconciliationStatus.MATCHED, "INFO", 10),
        (ReconciliationStatus.SETTLEMENT_DELAY, "LOW", 30),
        (ReconciliationStatus.INVALID_STATUS, "MEDIUM", 60),
        (ReconciliationStatus.AMOUNT_MISMATCH, "HIGH", 80),
        (ReconciliationStatus.REFUND_MISMATCH, "CRITICAL", 100),
    ],
)
def test_severity_and_priority_for_status(
    status: ReconciliationStatus,
    severity: Severity,
    priority: int,
) -> None:
    assert policy.severity_for(status) == severity
    assert policy.priority_for(status) == priority


def test_matched_has_no_recommended_action() -> None:
    assert (
        policy.recommended_action_for(ReconciliationStatus.MATCHED) is None
    )


def test_unresolved_follows_project_semantics() -> None:
    """UNRESOLVED means the engine refuses to guess → human review."""
    status = ReconciliationStatus.UNRESOLVED
    assert policy.severity_for(status) == "HIGH"
    action = policy.recommended_action_for(status).lower()
    assert "manually" in action
    assert "cannot safely classify" in action


def test_annotation_is_deterministic_and_pure() -> None:
    batch = generate_synthetic_batch()
    first = build_report(batch.payments, batch.settlements, batch.refunds)
    second = build_report(batch.payments, batch.settlements, batch.refunds)
    assert [r.model_dump() for r in first.results] == [
        r.model_dump() for r in second.results
    ]


def test_annotation_preserves_engine_fields() -> None:
    batch = generate_synthetic_batch()
    report = build_report(batch.payments, batch.settlements, batch.refunds)
    for result in report.results:
        annotated = dict(result.model_dump())
        stripped = policy.annotate_triage([result])[0].model_dump()
        for field in (
            "source_transaction_id",
            "matched_transaction_id",
            "status",
            "exception_type",
            "secondary_issues",
            "reason",
            "expected_amount_minor",
            "actual_amount_minor",
            "difference_minor",
            "currency",
        ):
            assert stripped[field] == annotated[field]


def test_shared_severity_vocabulary_is_reused() -> None:
    """No second severity taxonomy may exist next to signals.Severity."""
    for severity in policy.SEVERITY_BY_EXCEPTION.values():
        assert severity in {
            "INFO",
            "LOW",
            "MEDIUM",
            "HIGH",
            "CRITICAL",
        }

    from typing import Literal, get_args, get_origin

    def literal_args(annotation: object) -> tuple[object, ...]:
        """Extract Literal values from ``T``, ``Optional[T]`` or unions."""
        if get_origin(annotation) is Literal:
            return get_args(annotation)  # type: ignore[arg-type]
        for arg in get_args(annotation):  # type: ignore[arg-type]
            if get_origin(arg) is Literal:
                return get_args(arg)
        return ()

    from app.schemas.reconciliation import ReconciliationResult

    assert literal_args(
        FinancialSignal.model_fields["severity"].annotation
    ) == literal_args(ReconciliationResult.model_fields["severity"].annotation)
    assert literal_args(
        ReconciliationResult.model_fields["severity"].annotation
    ) == ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_policy_module_uses_no_llm_db_or_network() -> None:
    source = inspect.getsource(policy)
    forbidden = (
        "openai",
        "anthropic",
        "langchain",
        "langgraph",
        "sqlalchemy",
        "httpx",
        "requests",
        "get_db",
        "sessionmaker",
        "razorpay_client",
    )
    for token in forbidden:
        assert token not in source.lower(), token


def test_build_report_annotates_all_results_end_to_end() -> None:
    batch = generate_synthetic_batch()
    report = build_report(batch.payments, batch.settlements, batch.refunds)
    assert len(report.results) > 0
    matched_seen = False
    for result in report.results:
        assert result.severity in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert result.priority == EXPECTED_PRIORITY[result.severity]
        if result.is_exception:
            assert isinstance(result.recommended_action, str)
            assert result.recommended_action.strip()
        else:
            matched_seen = True
            assert result.status is ReconciliationStatus.MATCHED
            assert result.recommended_action is None
    assert matched_seen


def test_api_serves_triage_annotations(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ai/reconcile",
        json={
            "source": "synthetic",
            "seed": 42,
            "size": 100,
            "explain": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    exceptions = payload["exceptions"]
    assert exceptions, "canonical batch must surface exceptions"
    seen_statuses = {item["status"] for item in exceptions}
    assert ReconciliationStatus.MATCHED.value not in seen_statuses
    for item in exceptions:
        expected_sev = EXPECTED_SEVERITY[ReconciliationStatus(item["status"])]
        assert item["severity"] == expected_sev
        assert item["priority"] == EXPECTED_PRIORITY[expected_sev]
        assert isinstance(item["recommended_action"], str)
        assert item["recommended_action"].strip()


def test_api_unresolved_carries_manual_review_action(
    reconcile_client: TestClient,
) -> None:
    response = reconcile_client.post(
        "/api/v1/ai/reconcile",
        json={
            "source": "synthetic",
            "seed": 42,
            "size": 100,
            "explain": False,
        },
    )
    assert response.status_code == 200
    unresolved = [
        item
        for item in response.json()["exceptions"]
        if item["status"] == ReconciliationStatus.UNRESOLVED.value
    ]
    assert unresolved, "canonical batch includes UNRESOLVED cases"
    for item in unresolved:
        assert item["severity"] == "HIGH"
        assert "cannot safely classify" in item["recommended_action"]

