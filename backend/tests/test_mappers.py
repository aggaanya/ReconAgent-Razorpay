"""Mapper tests: normalized domain schemas -> ORM models."""

from datetime import datetime, timezone

from app.db.mappers import (
    order_from_normalized,
    payment_from_normalized,
    refund_from_normalized,
    settlement_from_normalized,
)
from app.db.models import Order, Payment, Refund, Settlement
from app.schemas import (
    NormalizedOrder,
    NormalizedPayment,
    NormalizedRefund,
    NormalizedSettlement,
)

UTC = timezone.utc


def _normalized_payment(**overrides) -> NormalizedPayment:
    values = dict(
        provider="razorpay",
        external_id="pay_ABCDEF123456",
        entity="payment",
        amount_minor=123456,
        currency="INR",
        status="captured",
        order_id="order_ABCDEF123456",
        invoice_id="inv_ABCDEF123456",
        method="upi",
        captured=True,
        description="Test payment",
        notes={"key": "value"},
        created_at=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return NormalizedPayment(**values)


def _normalized_settlement(**overrides) -> NormalizedSettlement:
    values = dict(
        provider="razorpay",
        external_id="setl_ABCDEF123456",
        entity="settlement",
        amount_minor=100000,
        status="processed",
        fees_minor=2500,
        tax_minor=450,
        utr="UTR123456789",
        currency="INR",
        created_at=datetime(2026, 1, 16, 6, 0, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return NormalizedSettlement(**values)


class TestPaymentMapping:
    def test_all_fields_mapped_exactly(self):
        model = payment_from_normalized(_normalized_payment())
        assert isinstance(model, Payment)
        assert model.razorpay_payment_id == "pay_ABCDEF123456"
        assert model.amount_minor == 123456  # exact minor units, no float
        assert model.currency == "INR"
        assert model.status == "captured"
        assert model.order_id == "order_ABCDEF123456"
        assert model.invoice_id == "inv_ABCDEF123456"
        assert model.method == "upi"
        assert model.captured is True
        assert model.description == "Test payment"
        assert model.notes == {"key": "value"}
        assert model.provider_created_at == datetime(2026, 1, 15, 10, 30, tzinfo=UTC)

    def test_optional_fields_default_to_none(self):
        minimal = NormalizedPayment(provider="razorpay", external_id="pay_X", amount_minor=1)
        model = payment_from_normalized(minimal)
        assert model.order_id is None
        assert model.invoice_id is None
        assert model.notes == {}
        assert model.provider_created_at is None

    def test_notes_copied_not_aliased(self):
        normalized = _normalized_payment()
        model = payment_from_normalized(normalized)
        model.notes["mutated"] = "yes"
        assert normalized.notes == {"key": "value"}


class TestSettlementMapping:
    def test_all_fields_mapped_exactly(self):
        model = settlement_from_normalized(_normalized_settlement())
        assert isinstance(model, Settlement)
        assert model.razorpay_settlement_id == "setl_ABCDEF123456"
        assert model.amount_minor == 100000
        assert model.fees_minor == 2500
        assert model.tax_minor == 450
        assert model.status == "processed"
        assert model.utr == "UTR123456789"
        assert model.provider_created_at == datetime(2026, 1, 16, 6, 0, tzinfo=UTC)

    def test_defaults_preserved(self):
        minimal = NormalizedSettlement(
            provider="razorpay", external_id="setl_X", amount_minor=5
        )
        model = settlement_from_normalized(minimal)
        assert model.fees_minor == 0
        assert model.tax_minor == 0
        assert model.utr is None


def _normalized_order(**overrides) -> NormalizedOrder:
    values = dict(
        provider="razorpay",
        external_id="order_ABCDEF123456",
        entity="order",
        amount_minor=123456,
        amount_paid_minor=100000,
        amount_due_minor=23456,
        currency="INR",
        status="paid",
        receipt="rcpt_001",
        notes={"campaign": "diwali"},
        created_at=datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return NormalizedOrder(**values)


class TestOrderMapping:
    def test_all_fields_mapped_exactly(self):
        model = order_from_normalized(_normalized_order())
        assert isinstance(model, Order)
        assert model.razorpay_order_id == "order_ABCDEF123456"
        # Exact minor-unit amounts, all three preserved without arithmetic.
        assert model.amount_minor == 123456
        assert model.amount_paid_minor == 100000
        assert model.amount_due_minor == 23456
        assert model.currency == "INR"
        assert model.status == "paid"
        assert model.receipt == "rcpt_001"
        assert model.notes == {"campaign": "diwali"}
        assert model.provider_created_at == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    def test_optional_fields_default_to_none(self):
        minimal = NormalizedOrder(
            provider="razorpay", external_id="order_X", amount_minor=1
        )
        model = order_from_normalized(minimal)
        assert model.currency is None
        assert model.receipt is None
        assert model.notes == {}
        assert model.provider_created_at is None

    def test_notes_copied_not_aliased(self):
        normalized = _normalized_order()
        model = order_from_normalized(normalized)
        model.notes["mutated"] = "yes"
        assert normalized.notes == {"campaign": "diwali"}


def _normalized_refund(**overrides) -> NormalizedRefund:
    values = dict(
        provider="razorpay",
        external_id="rfnd_ABCDEF123456",
        entity="refund",
        razorpay_payment_id="pay_ABCDEF123456",
        amount_minor=50000,
        currency="INR",
        status="processed",
        speed="normal",
        notes={"reason": "duplicate"},
        created_at=datetime(2026, 1, 17, 8, 15, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return NormalizedRefund(**values)


class TestRefundMapping:
    def test_all_fields_mapped_exactly(self):
        model = refund_from_normalized(_normalized_refund())
        assert isinstance(model, Refund)
        assert model.razorpay_refund_id == "rfnd_ABCDEF123456"
        assert model.razorpay_payment_id == "pay_ABCDEF123456"
        assert model.amount_minor == 50000
        assert model.currency == "INR"
        assert model.status == "processed"
        assert model.speed == "normal"
        assert model.notes == {"reason": "duplicate"}
        assert model.provider_created_at == datetime(2026, 1, 17, 8, 15, tzinfo=UTC)

    def test_optional_fields_default_to_none(self):
        minimal = NormalizedRefund(
            provider="razorpay",
            external_id="rfnd_X",
            razorpay_payment_id="pay_X",
            amount_minor=5,
        )
        model = refund_from_normalized(minimal)
        assert model.speed is None
        assert model.notes == {}
        assert model.provider_created_at is None
