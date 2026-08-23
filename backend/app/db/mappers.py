"""Mappers: provider-independent domain schemas -> ORM models.

Keeps persistence mapping in exactly one place. These functions are pure:
they construct model instances and never touch the session.
"""

from app.db.models import Order, Payment, Refund, Settlement
from app.schemas import (
    NormalizedOrder,
    NormalizedPayment,
    NormalizedRefund,
    NormalizedSettlement,
)


def payment_from_normalized(normalized: NormalizedPayment) -> Payment:
    """Map a :class:`NormalizedPayment` to a :class:`Payment` row.

    Amounts pass through as exact minor-unit integers; ids/timestamps are
    preserved verbatim; unknown provider fields were already dropped by the
    wire parser upstream of this layer.
    """
    return Payment(
        razorpay_payment_id=normalized.external_id,
        entity=normalized.entity,
        amount_minor=normalized.amount_minor,
        currency=normalized.currency,
        status=normalized.status,
        order_id=normalized.order_id,
        invoice_id=normalized.invoice_id,
        method=normalized.method,
        captured=normalized.captured,
        fee_minor=normalized.fee_minor,
        tax_minor=normalized.tax_minor,
        description=normalized.description,
        notes=dict(normalized.notes),
        provider_created_at=normalized.created_at,
    )


def settlement_from_normalized(normalized: NormalizedSettlement) -> Settlement:
    """Map a :class:`NormalizedSettlement` to a :class:`Settlement` row."""
    return Settlement(
        razorpay_settlement_id=normalized.external_id,
        entity=normalized.entity,
        amount_minor=normalized.amount_minor,
        fees_minor=normalized.fees_minor,
        tax_minor=normalized.tax_minor,
        currency=normalized.currency,
        status=normalized.status,
        utr=normalized.utr,
        provider_created_at=normalized.created_at,
    )


def order_from_normalized(normalized: NormalizedOrder) -> Order:
    """Map a :class:`NormalizedOrder` to an :class:`Order` row.

    Amounts pass through as exact minor-unit integers; ids/timestamps are
    preserved verbatim.
    """
    return Order(
        razorpay_order_id=normalized.external_id,
        entity=normalized.entity,
        amount_minor=normalized.amount_minor,
        amount_paid_minor=normalized.amount_paid_minor,
        amount_due_minor=normalized.amount_due_minor,
        currency=normalized.currency,
        status=normalized.status,
        receipt=normalized.receipt,
        notes=dict(normalized.notes),
        provider_created_at=normalized.created_at,
    )


def refund_from_normalized(normalized: NormalizedRefund) -> Refund:
    """Map a :class:`NormalizedRefund` to a :class:`Refund` row."""
    return Refund(
        razorpay_refund_id=normalized.external_id,
        entity=normalized.entity,
        razorpay_payment_id=normalized.razorpay_payment_id,
        amount_minor=normalized.amount_minor,
        currency=normalized.currency,
        status=normalized.status,
        speed=normalized.speed,
        notes=dict(normalized.notes),
        provider_created_at=normalized.created_at,
    )
