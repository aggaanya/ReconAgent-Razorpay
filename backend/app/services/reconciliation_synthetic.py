"""Deterministic synthetic reconciliation dataset (Track 04 evaluation).

Generates a seeded batch of payment / settlement-line / refund records
containing both clean traffic and intentionally corrupted cases, plus
**ground truth**: the expected reconciliation outcome of every case,
including the expected settlement arithmetic.

Ground-truth discipline (the whole point of this module):

- The generator is the ONLY producer of ground truth.
- Nothing in ``app.services.reconciliation`` or the AI layers accepts
  expected outcomes as input — :func:`reconcile` sees payments,
  settlement lines, and refunds, nothing else. Ground truth flows
  exclusively into :func:`app.services.reconciliation.evaluate_ground_truth`,
  tests, and the benchmark script.

Determinism contract: for a fixed ``(seed, size)`` the batch — ids,
amounts, currencies, dates, case placement, ground truth — is
byte-for-byte identical across runs and platforms. Amounts come from
index arithmetic (no floating point), dates from a fixed epoch, and the
only stochastic step is a seeded shuffle of case order. Different seeds
relocate cases; they never change counts.

Default distribution per 100 cases (docs/RECONCILIATION_ARCHITECTURE.md
§6): clean traffic (gross and net-of-components matches) plus
deliberately broken cases spanning every documented exception type,
including the financially realistic families — fee/tax deduction,
processed refunds, refund anomalies, late settlement, and unexplained
settlement differences. Other sizes scale proportionally
(largest-remainder rounding; remainders go to ``exact_match``), so rare
case types may be absent in very small batches.

All monetary figures are fabricated test data. They deliberately do NOT
encode Razorpay fee schedules or payout SLAs — the engine only ever
consumes whatever components are recorded on its inputs.
"""

import random
from datetime import date, timedelta

from pydantic import BaseModel, Field

from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    REFUND_STATUS_PROCESSED,
    GroundTruthEntry,
    ReconciliationPayment,
    ReconciliationRefund,
    ReconciliationSettlementLine,
    ReconciliationStatus,
)

DEFAULT_SEED = 42
DEFAULT_SIZE = 100

#: Fixed epoch for deterministic provider-clock dates.
_EPOCH = date(2026, 1, 1)

#: Case-type proportions per 100 records. Sum MUST stay exactly 100;
#: a drift test pins this.
CASE_DISTRIBUTION: dict[ReconciliationStatus, int] = {
    ReconciliationStatus.MATCHED: 58,
    ReconciliationStatus.AMOUNT_MISMATCH: 6,
    ReconciliationStatus.MISSING_SETTLEMENT: 5,
    ReconciliationStatus.DUPLICATE_SETTLEMENT: 3,
    ReconciliationStatus.MISSING_PAYMENT: 3,
    ReconciliationStatus.CURRENCY_MISMATCH: 2,
    ReconciliationStatus.INVALID_STATUS: 2,
    ReconciliationStatus.UNRESOLVED: 2,
    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE: 7,
    ReconciliationStatus.REFUND_MISMATCH: 7,
    ReconciliationStatus.SETTLEMENT_DELAY: 5,
}

_BASE_CURRENCY = "INR"
_FOREIGN_CURRENCY = "USD"


def _scaled_distribution(size: int) -> list[ReconciliationStatus]:
    """Proportional case list of exactly ``size`` entries (deterministic)."""
    total_weight = sum(CASE_DISTRIBUTION.values())
    cases: list[ReconciliationStatus] = []
    remainder_cases = 0
    allocated = 0
    for status, weight in CASE_DISTRIBUTION.items():
        exact = size * weight / total_weight
        count = int(exact)
        remainder_cases += exact - count
        # Largest-remainder style: track fractional leftovers; any case
        # whose fractional part crosses a whole unit gets the extra slot.
        while remainder_cases >= 1 and count < size - allocated:
            count += 1
            remainder_cases -= 1
        cases.extend([status] * count)
        allocated += count
    # Rounding guard: top up with MATCHED (the dominant class) if the
    # floor arithmetic under-shot, or trim if it over-shot.
    while len(cases) > size:
        cases.remove(ReconciliationStatus.MATCHED)
    while len(cases) < size:
        cases.append(ReconciliationStatus.MATCHED)
    return cases


def _payment_amount(index: int, seed: int) -> int:
    """Stable pseudo-varied minor-unit amount from pure integer math."""
    return 100_000 + ((index * 7_919 + seed * 104_729) % 900_000)


def _fee_amount(index: int) -> int:
    """Fabricated recorded fee (NOT a Razorpay rate — synthetic data)."""
    return 1_200 + (index * 31) % 4_000


def _tax_amount(fee: int) -> int:
    """Fabricated tax-on-fee derived arithmetically from the fee itself."""
    return fee // 5


class SyntheticBatch(BaseModel):
    """One generated batch plus its evaluation-only ground truth."""

    seed: int
    size: int = Field(ge=0)
    payments: list[ReconciliationPayment]
    settlements: list[ReconciliationSettlementLine]
    refunds: list[ReconciliationRefund] = Field(default_factory=list)
    #: Expected outcome per case — NEVER an input to the engine.
    ground_truth: list[GroundTruthEntry]


def generate_synthetic_batch(
    *,
    seed: int = DEFAULT_SEED,
    size: int = DEFAULT_SIZE,
) -> SyntheticBatch:
    """Build a deterministic mixed batch of ``size`` reconciliation cases.

    Timing-aware evaluation requires passing the configured tolerance to
    the engine (``max_settlement_delay_days``); ``DEFAULT_MAX_
    SETTLEMENT_DELAY_DAYS`` is the value every in-repo caller uses.
    """
    if size < 0:
        raise ValueError("size must be non-negative")

    rng = random.Random(seed)
    case_types = _scaled_distribution(size)
    rng.shuffle(case_types)  # seeded: deterministic placement

    payments: list[ReconciliationPayment] = []
    settlements: list[ReconciliationSettlementLine] = []
    refunds: list[ReconciliationRefund] = []
    ground_truth: list[GroundTruthEntry] = []

    def next_payment_id(step: int) -> str:
        return f"pay_{seed % 100:02d}_{step:04d}"

    def next_settlement_id(step: int) -> str:
        return f"set_{seed % 100:02d}_{step:04d}"

    def next_refund_id(step: int) -> str:
        return f"rfnd_{seed % 100:02d}_{step:04d}"

    for case_type in case_types:
        step = len(ground_truth)
        payment_id = next_payment_id(step)
        amount = _payment_amount(step, seed)
        created_on = _EPOCH + timedelta(days=step)

        # Helper: record truth with the full arithmetic contract.
        def truth(
            case: str,
            status: ReconciliationStatus,
            *,
            pid: str | None = payment_id,
            line_ids: tuple[str, ...] = (),
            expected: int | None = None,
            actual: int | None = None,
            fragment: str | None = None,
            secondary: tuple[ReconciliationStatus, ...] = (),
        ) -> None:
            ground_truth.append(
                GroundTruthEntry(
                    case_id=case,
                    payment_id=pid,
                    settlement_ids=line_ids,
                    expected_status=status,
                    expected_exception_type=(
                        status if status is not ReconciliationStatus.MATCHED
                        else None
                    ),
                    expected_amount_minor=expected,
                    actual_amount_minor=actual,
                    expected_reason_fragment=fragment,
                    expected_secondary_issues=secondary,
                )
            )

        if case_type is ReconciliationStatus.MATCHED:
            # Three deterministic sub-shapes exercise every matching path:
            #   0) legacy gross match — no financial components;
            #   1) net of recorded fee + tax;
            #   2) net of a processed (partial) refund + fee + tax.
            settlement_id = next_settlement_id(step)
            shape = step % 3
            fee = _fee_amount(step)
            tax = _tax_amount(fee)
            if shape == 0:
                settled = amount
                expected = amount
                fragment = "exact minor-unit amount"
                payment = ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                )
            elif shape == 1:
                settled = amount - fee - tax
                expected = settled
                fragment = "expected net amount"
                payment = ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                    fee_minor=fee,
                    tax_minor=tax,
                    created_on=created_on,
                )
            else:
                refund_total = amount // 10
                settled = amount - refund_total - fee - tax
                expected = settled
                fragment = "expected net amount"
                payment = ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                    fee_minor=fee,
                    tax_minor=tax,
                    created_on=created_on,
                )
                refunds.append(
                    ReconciliationRefund(
                        refund_id=next_refund_id(step),
                        payment_id=payment_id,
                        amount_minor=refund_total,
                        currency=_BASE_CURRENCY,
                        status=REFUND_STATUS_PROCESSED,
                        
                    )
                )
            payments.append(payment)
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=settled,
                    currency=_BASE_CURRENCY,
                    settled_on=created_on,  # same-day: never a delay case
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.MATCHED,
                line_ids=(settlement_id,),
                expected=expected,
                actual=settled,
                fragment=fragment,
            )

        elif case_type is ReconciliationStatus.AMOUNT_MISMATCH:
            settlement_id = next_settlement_id(step)
            # Legacy shape on purpose: NO components recorded, so the
            # engine classifies the raw difference as AMOUNT_MISMATCH.
            delta = 101 + (step * 13) % 5_000
            settled = amount + delta if step % 2 == 0 else amount - delta
            settled = max(settled, 0)
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=settled,
                    currency=_BASE_CURRENCY,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.AMOUNT_MISMATCH,
                line_ids=(settlement_id,),
                expected=amount,
                actual=settled,
                fragment="differs from the payment",
            )

        elif case_type is ReconciliationStatus.MISSING_SETTLEMENT:
            # Parity variants: plain missing settlement, and missing
            # settlement despite processed refunds (R2 still wins; the
            # reason must surface the refund context).
            if step % 2 == 0:
                payments.append(
                    ReconciliationPayment(
                        payment_id=payment_id,
                        amount_minor=amount,
                        currency=_BASE_CURRENCY,
                        status="captured",
                    )
                )
                fragment = "No settlement line"
            else:
                fee = _fee_amount(step)
                payments.append(
                    ReconciliationPayment(
                        payment_id=payment_id,
                        amount_minor=amount,
                        currency=_BASE_CURRENCY,
                        status="captured",
                        fee_minor=fee,
                        tax_minor=_tax_amount(fee),
                        created_on=created_on,
                    )
                )
                refunds.append(
                    ReconciliationRefund(
                        refund_id=next_refund_id(step),
                        payment_id=payment_id,
                        amount_minor=amount // 10,
                        currency=_BASE_CURRENCY,
                        status=REFUND_STATUS_PROCESSED,
                    )
                )
                fragment = "processed refund"
            truth(
                payment_id,
                ReconciliationStatus.MISSING_SETTLEMENT,
                expected=amount,
                actual=None,
                fragment=fragment,
            )

        elif case_type is ReconciliationStatus.DUPLICATE_SETTLEMENT:
            first_id = next_settlement_id(step)
            second_id = f"{first_id}d"
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                )
            )
            for line_id in (first_id, second_id):
                settlements.append(
                    ReconciliationSettlementLine(
                        settlement_id=line_id,
                        payment_id=payment_id,
                        amount_minor=amount,
                        currency=_BASE_CURRENCY,
                    )
                )
            truth(
                payment_id,
                ReconciliationStatus.DUPLICATE_SETTLEMENT,
                line_ids=(first_id, second_id),
                expected=amount,
                actual=amount * 2,
                fragment="attribution is ambiguous",
            )

        elif case_type is ReconciliationStatus.MISSING_PAYMENT:
            # Two corruption shapes sharing the MISSING_PAYMENT verdict:
            # even steps — a phantom settlement attribution; odd steps —
            # an orphaned processed refund whose parent never existed.
            if step % 2 == 0:
                settlement_id = next_settlement_id(step)
                settlements.append(
                    ReconciliationSettlementLine(
                        settlement_id=settlement_id,
                        payment_id=payment_id,  # points nowhere on purpose
                        amount_minor=amount,
                        currency=_BASE_CURRENCY,
                    )
                )
                truth(
                    payment_id,
                    ReconciliationStatus.MISSING_PAYMENT,
                    pid=None,
                    line_ids=(settlement_id,),
                    actual=None,
                    fragment="does not exist in the reconciled batch",
                )
            else:
                refund_id = next_refund_id(step)
                refund_amount = 25_000 + (step * 977) % 60_000
                refunds.append(
                    ReconciliationRefund(
                        refund_id=refund_id,
                        payment_id=payment_id,  # points nowhere on purpose
                        amount_minor=refund_amount,
                        currency=_BASE_CURRENCY,
                        status=REFUND_STATUS_PROCESSED,
                    )
                )
                truth(
                    refund_id,  # engine anchors on the refund id
                    ReconciliationStatus.MISSING_PAYMENT,
                    pid=None,
                    actual=refund_amount,
                    fragment="parent payment",
                )

        elif case_type is ReconciliationStatus.CURRENCY_MISMATCH:
            settlement_id = next_settlement_id(step)
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_FOREIGN_CURRENCY,
                    status="captured",
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.CURRENCY_MISMATCH,
                line_ids=(settlement_id,),
                expected=amount,
                actual=amount,
                fragment="were not compared across currencies",
            )

        elif case_type is ReconciliationStatus.INVALID_STATUS:
            settlement_id = next_settlement_id(step)
            # Alternate between the two documented corruption shapes:
            # known-but-ineligible status, and unknown vocabulary.
            status: str = "failed" if step % 2 == 0 else "processing"
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status=status,
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.INVALID_STATUS,
                line_ids=(settlement_id,),
                expected=amount,
                actual=amount,
                fragment="settlement-eligible" if status == "failed"
                else "vocabulary",
            )

        elif case_type is (
            ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        ):
            # Fee and tax ARE recorded (components accounted), yet the
            # settlement differs from the computed net by an unexplained
            # residual — the exact case the legacy AMOUNT_MISMATCH could
            # not describe. Odd steps additionally settle one day beyond
            # the configured delay tolerance: financial correctness
            # outranks timeliness, so the engine must keep the mismatch
            # as the primary verdict and attach SETTLEMENT_DELAY as a
            # compound secondary issue (pinned via expected_secondary_
            # issues; even steps stay same-day with none).
            settlement_id = next_settlement_id(step)
            fee = _fee_amount(step)
            tax = _tax_amount(fee)
            expected_net = amount - fee - tax
            residual = 100 + (step * 17) % 3_000
            settled = expected_net - residual
            late = step % 2 == 1
            settled_on = (
                created_on
                + timedelta(days=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS + 1)
                if late
                else created_on
            )
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                    fee_minor=fee,
                    tax_minor=tax,
                    created_on=created_on,
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=settled,
                    currency=_BASE_CURRENCY,
                    settled_on=settled_on,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE,
                line_ids=(settlement_id,),
                expected=expected_net,
                actual=settled,
                fragment="no recorded",
                secondary=(
                    (ReconciliationStatus.SETTLEMENT_DELAY,) if late else ()
                ),
            )

        elif case_type is ReconciliationStatus.REFUND_MISMATCH:
            # Two integrity violations, alternating deterministically:
            # even steps — processed refunds exceed the payment amount;
            # odd steps — a processed refund in a foreign currency.
            settlement_id = next_settlement_id(step)
            first_refund_id = next_refund_id(step)
            if step % 2 == 0:
                half = amount // 2
                refunds.extend(
                    [
                        ReconciliationRefund(
                            refund_id=first_refund_id,
                            payment_id=payment_id,
                            amount_minor=half,
                            currency=_BASE_CURRENCY,
                            status=REFUND_STATUS_PROCESSED,
                        ),
                        ReconciliationRefund(
                            refund_id=f"{first_refund_id}b",
                            payment_id=payment_id,
                            amount_minor=amount - half + 5_000,
                            currency=_BASE_CURRENCY,
                            status=REFUND_STATUS_PROCESSED,
                        ),
                    ]
                )
                fragment = "exceeding the payment amount"
            else:
                refunds.append(
                    ReconciliationRefund(
                        refund_id=first_refund_id,
                        payment_id=payment_id,
                        amount_minor=amount // 10,
                        currency=_FOREIGN_CURRENCY,
                        status=REFUND_STATUS_PROCESSED,
                    )
                )
                fragment = "different currency"
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                    created_on=created_on,
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    settled_on=created_on,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.REFUND_MISMATCH,
                line_ids=(settlement_id,),
                expected=amount,
                actual=amount,
                fragment=fragment,
            )

        elif case_type is ReconciliationStatus.SETTLEMENT_DELAY:
            # Perfect money match, late arrival: only the configured
            # timing window makes this an exception.
            settlement_id = next_settlement_id(step)
            delay_days = DEFAULT_MAX_SETTLEMENT_DELAY_DAYS + 1 + step % 4
            settled_on = created_on + timedelta(days=delay_days)
            payments.append(
                ReconciliationPayment(
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    status="captured",
                    created_on=created_on,
                )
            )
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=payment_id,
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                    settled_on=settled_on,
                )
            )
            truth(
                payment_id,
                ReconciliationStatus.SETTLEMENT_DELAY,
                line_ids=(settlement_id,),
                expected=amount,
                actual=amount,
                fragment="beyond the configured tolerance",
            )

        else:  # UNRESOLVED
            settlement_id = next_settlement_id(step)
            settlements.append(
                ReconciliationSettlementLine(
                    settlement_id=settlement_id,
                    payment_id=None,  # unusable attribution → R7
                    amount_minor=amount,
                    currency=_BASE_CURRENCY,
                )
            )
            truth(
                settlement_id,  # engine anchors on the line
                ReconciliationStatus.UNRESOLVED,
                pid=None,
                line_ids=(settlement_id,),
                actual=amount,
                fragment="no usable payment",
            )

    return SyntheticBatch(
        seed=seed,
        size=size,
        payments=payments,
        settlements=settlements,
        refunds=refunds,
        ground_truth=ground_truth,
    )


__all__ = [
    "CASE_DISTRIBUTION",
    "DEFAULT_SEED",
    "DEFAULT_SIZE",
    "SyntheticBatch",
    "generate_synthetic_batch",
]
