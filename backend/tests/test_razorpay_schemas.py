"""Wire-model parsing tests: Razorpay payloads -> typed DTOs.

Covers the happy path plus the typed invalid-response contract:
malformed bodies raise ``RazorpayInvalidResponseError`` (a subclass of
``RazorpayError``), never pydantic exceptions or bare dicts.
"""

from app.integrations.razorpay import (
    RazorpayError,
    RazorpayInvalidResponseError,
    parse_order_collection,
    parse_payment_collection,
    parse_refund_collection,
    parse_settlement_collection,
)


class TestPaymentCollectionParsing:
    def test_valid_payload_parses(self) -> None:
        collection = parse_payment_collection(
            {
                "entity": "collection",
                "count": 2,
                "items": [
                    {
                        "id": "pay_1",
                        "entity": "payment",
                        "amount": 500,
                        "currency": "INR",
                        "status": "captured",
                        "order_id": "order_1",
                    },
                    {"id": "pay_2", "amount": 700},
                ],
            }
        )
        assert [p.id for p in collection.items] == ["pay_1", "pay_2"]
        assert collection.items[0].status == "captured"
        assert collection.items[1].amount == 700
        assert collection.count == 2

    def test_unknown_fields_are_ignored(self) -> None:
        collection = parse_payment_collection(
            {"count": 1, "items": [{"id": "pay_1", "amount": 1,
                                    "brand_new_provider_field": {"x": 1}}]}
        )
        assert collection.items[0].id == "pay_1"

    def test_non_dict_payload_is_invalid_response(self) -> None:
        for payload in ([], "nope", None, 42):
            try:
                parse_payment_collection(payload)
            except RazorpayInvalidResponseError:
                continue
            raise AssertionError(f"expected invalid response for {payload!r}")

    def test_items_missing_required_fields_is_invalid_response(self) -> None:
        try:
            parse_payment_collection({"items": [{"id": "pay_1"}]})  # no amount
        except RazorpayInvalidResponseError as exc:
            assert isinstance(exc, RazorpayError)
            assert "payments" in str(exc)
        else:
            raise AssertionError("missing amount must be rejected")


class TestSettlementCollectionParsing:
    def test_valid_payload_parses_with_documented_defaults(self) -> None:
        collection = parse_settlement_collection(
            {
                "count": 1,
                "items": [
                    {
                        "id": "setl_1",
                        "entity": "settlement",
                        "amount": 100000,
                        "status": "processed",
                        "fees": 2500,
                        "tax": 450,
                        "utr": "UTR123",
                        "created_at": 1768000000,
                    }
                ],
            }
        )
        item = collection.items[0]
        assert item.utr == "UTR123"
        assert item.fees == 2500 and item.tax == 450

    def test_missing_id_is_invalid_response(self) -> None:
        try:
            parse_settlement_collection({"items": [{"amount": 5}]})
        except RazorpayInvalidResponseError as exc:
            assert isinstance(exc, RazorpayError)
        else:
            raise AssertionError("missing id must be rejected")


class TestOrderCollectionParsing:
    def test_valid_payload_parses_with_documented_defaults(self) -> None:
        collection = parse_order_collection(
            {
                "entity": "collection",
                "count": 2,
                "items": [
                    {
                        "id": "order_1",
                        "entity": "order",
                        "amount": 50000,
                        "amount_paid": 0,
                        "amount_due": 50000,
                        "currency": "INR",
                        "receipt": "rcpt_1",
                        "status": "created",
                        "notes": {"campaign": "diwali"},
                        "created_at": 1768000000,
                    },
                    {"id": "order_2", "amount": 700},
                ],
            }
        )
        first, second = collection.items
        assert first.status == "created"
        assert first.receipt == "rcpt_1"
        assert first.notes == {"campaign": "diwali"}
        # Documented defaults when the provider omits them.
        assert second.amount_paid == 0 and second.amount_due == 0
        assert collection.count == 2

    def test_unknown_fields_are_ignored(self) -> None:
        collection = parse_order_collection(
            {"count": 1, "items": [{"id": "order_1", "amount": 1,
                                    "offer_id": "offer_X"}]}
        )
        assert collection.items[0].id == "order_1"

    def test_non_dict_payload_is_invalid_response(self) -> None:
        for payload in ([], "nope", None, 42):
            try:
                parse_order_collection(payload)
            except RazorpayInvalidResponseError:
                continue
            raise AssertionError(f"expected invalid response for {payload!r}")

    def test_missing_amount_is_invalid_response(self) -> None:
        try:
            parse_order_collection({"items": [{"id": "order_1"}]})
        except RazorpayInvalidResponseError as exc:
            assert isinstance(exc, RazorpayError)
            assert "orders" in str(exc)
        else:
            raise AssertionError("missing amount must be rejected")


class TestRefundCollectionParsing:
    def test_valid_payload_parses_with_documented_defaults(self) -> None:
        collection = parse_refund_collection(
            {
                "entity": "collection",
                "count": 1,
                "items": [
                    {
                        "id": "rfnd_1",
                        "entity": "refund",
                        "payment_id": "pay_1",
                        "amount": 25000,
                        "currency": "INR",
                        "status": "processed",
                        "notes": {"reason": "duplicate"},
                        "created_at": 1768000100,
                    }
                ],
            }
        )
        item = collection.items[0]
        assert item.payment_id == "pay_1"
        assert item.amount == 25000
        assert item.status == "processed"
        # Razorpay's documented default payout speed.
        assert item.speed is None

    def test_speed_preserved_when_provided(self) -> None:
        collection = parse_refund_collection(
            {"count": 1, "items": [
                {"id": "rfnd_1", "payment_id": "pay_1", "amount": 5,
                 "speed": "optimum"},
            ]}
        )
        assert collection.items[0].speed == "optimum"

    def test_non_dict_payload_is_invalid_response(self) -> None:
        for payload in ([], "nope", None):
            try:
                parse_refund_collection(payload)
            except RazorpayInvalidResponseError:
                continue
            raise AssertionError(f"expected invalid response for {payload!r}")

    def test_missing_payment_id_is_invalid_response(self) -> None:
        try:
            parse_refund_collection({"items": [{"id": "rfnd_1", "amount": 5}]})
        except RazorpayInvalidResponseError as exc:
            assert isinstance(exc, RazorpayError)
            assert "refunds" in str(exc)
        else:
            raise AssertionError("missing payment_id must be rejected")
