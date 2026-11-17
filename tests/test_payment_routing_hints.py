from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.payment import PaymentCreate, PaymentResponse


def test_payment_create_normalizes_routing_hints():
    payment = PaymentCreate(
        customer_account_id=uuid4(),
        amount_minor=12500,
        provider="auto",
        payment_method="upi",
        bank_code="hdfc",
    )

    assert payment.payment_method == "UPI"
    assert payment.bank_code == "HDFC"


def test_payment_create_rejects_invalid_routing_hint():
    with pytest.raises(ValidationError):
        PaymentCreate(
            customer_account_id=uuid4(),
            amount_minor=12500,
            payment_method="UPI/INJECTION",
        )


def test_payment_response_accepts_auto_before_gateway_order():
    assert "auto" in PaymentResponse.model_fields["provider"].annotation.__args__
