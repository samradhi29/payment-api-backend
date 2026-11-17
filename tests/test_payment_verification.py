
import pytest
from sqlalchemy import select, func

from app.models.account import Account
from app.models.payment import Payment
from app.models.ledger_transaction import LedgerTransaction
from app.models.ledger_entry import LedgerEntry

from app.providers.base import GatewayPayment

from app.services.payment_verification_service import (
    verify_and_post_payment,
    recover_and_post_payment,
    PaymentVerificationError,
    CLEARING_ACCOUNT_NUMBER,
)


@pytest.fixture
def clearing_account(account_factory):
    return account_factory(
        account_number=CLEARING_ACCOUNT_NUMBER,
        name="Razorpay Clearing",
        account_type="asset",
        normal_side="debit",
        currency="INR",
        balance=0,
        allow_negative=False,
    )


@pytest.fixture
def pending_payment(db_session, accounts):
    payment = Payment(
        customer_account_id=accounts["customer"].id,
        merchant_account_id=accounts["merchant"].id,
        amount_minor=10000,
        currency="INR",
        status="pending",
        provider="razorpay",
        provider_order_id="order_test_001",
        request_hash="a" * 64,
    )

    db_session.add(payment)
    db_session.commit()
    db_session.refresh(payment)

    return payment


@pytest.fixture
def mock_razorpay(monkeypatch):
    import app.services.payment_verification_service as verification

    monkeypatch.setattr(
        verification,
        "verify_razorpay_signature",
        lambda *args: True,
    )

    monkeypatch.setattr(
        verification,
        "fetch_razorpay_payment",
        lambda payment_id: {
            "id": payment_id,
            "order_id": "order_test_001",
            "amount": 10000,
            "currency": "INR",
            "status": "captured",
        },
    )


def test_successful_payment_posts_balanced_ledger(
    db_session,
    accounts,
    clearing_account,
    pending_payment,
    mock_razorpay,
):
    stored_clearing_account = db_session.execute(
        select(Account).where(
            Account.account_number == CLEARING_ACCOUNT_NUMBER
        )
    ).scalar_one_or_none()

    assert stored_clearing_account is not None
    assert stored_clearing_account.id == clearing_account.id

    verify_and_post_payment(
        db=db_session,
        payment_id=pending_payment.id,
        razorpay_order_id="order_test_001",
        razorpay_payment_id="pay_test_001",
        razorpay_signature="valid_test_signature",
    )

    db_session.refresh(pending_payment)

    assert pending_payment.status == "succeeded"
    assert pending_payment.provider_payment_id == "pay_test_001"

    transaction = db_session.execute(
        select(LedgerTransaction).where(
            LedgerTransaction.payment_id == pending_payment.id
        )
    ).scalar_one()

    entries = db_session.execute(
        select(LedgerEntry).where(
            LedgerEntry.transaction_id == transaction.id
        )
    ).scalars().all()

    assert len(entries) == 2

    debit = next(entry for entry in entries if entry.side == "debit")
    credit = next(entry for entry in entries if entry.side == "credit")

    assert debit.account_id == clearing_account.id
    assert credit.account_id == accounts["merchant"].id

    assert debit.amount_minor == 10000
    assert credit.amount_minor == 10000

    total_debit = sum(
        entry.amount_minor
        for entry in entries
        if entry.side == "debit"
    )

    total_credit = sum(
        entry.amount_minor
        for entry in entries
        if entry.side == "credit"
    )

    assert total_debit == total_credit

    db_session.refresh(clearing_account)
    db_session.refresh(accounts["merchant"])
    db_session.refresh(accounts["customer"])

    assert clearing_account.current_balance_minor == 10000
    assert accounts["merchant"].current_balance_minor == 10000
    assert accounts["customer"].current_balance_minor == 0


def test_repeated_verification_does_not_duplicate_ledger(
    db_session,
    accounts,
    clearing_account,
    pending_payment,
    mock_razorpay,
):
    for _ in range(2):
        verify_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
            razorpay_order_id="order_test_001",
            razorpay_payment_id="pay_test_001",
            razorpay_signature="valid_test_signature",
        )

    transaction_count = db_session.scalar(
        select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.payment_id == pending_payment.id
        )
    )

    entry_count = db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    )

    db_session.refresh(clearing_account)
    db_session.refresh(accounts["merchant"])

    assert transaction_count == 1
    assert entry_count == 2

    assert clearing_account.current_balance_minor == 10000
    assert accounts["merchant"].current_balance_minor == 10000


def test_invalid_signature_does_not_post_ledger(
    db_session,
    clearing_account,
    pending_payment,
    monkeypatch,
):
    import app.services.payment_verification_service as verification

    monkeypatch.setattr(
        verification,
        "verify_razorpay_signature",
        lambda *args: False,
    )

    with pytest.raises(PaymentVerificationError):
        verify_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
            razorpay_order_id="order_test_001",
            razorpay_payment_id="pay_test_001",
            razorpay_signature="invalid_signature",
        )

    db_session.rollback()
    db_session.refresh(pending_payment)

    assert pending_payment.status == "pending"

    assert db_session.scalar(
        select(func.count()).select_from(LedgerTransaction)
    ) == 0

    assert db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    ) == 0


def test_wrong_gateway_amount_does_not_post_ledger(
    db_session,
    pending_payment,
    clearing_account,
    monkeypatch,
):
    import app.services.payment_verification_service as verification

    monkeypatch.setattr(
        verification,
        "verify_razorpay_signature",
        lambda *args: True,
    )

    monkeypatch.setattr(
        verification,
        "fetch_razorpay_payment",
        lambda payment_id: {
            "id": payment_id,
            "order_id": "order_test_001",
            "amount": 5000,
            "currency": "INR",
            "status": "captured",
        },
    )

    with pytest.raises(PaymentVerificationError):
        verify_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
            razorpay_order_id="order_test_001",
            razorpay_payment_id="pay_test_001",
            razorpay_signature="valid_test_signature",
        )

    db_session.rollback()

    assert db_session.scalar(
        select(func.count()).select_from(LedgerTransaction)
    ) == 0

    assert db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    ) == 0


# ---------------------------------------------------------
# PAYMENT RECOVERY TESTS
# ---------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "provider_payment_id"),
    [
        ("razorpay", "pay_razorpay_recovery_001"),
        ("cashfree", "cf_payment_recovery_001"),
        ("payu", "payu_payment_recovery_001"),
    ],
)
def test_successful_recovery_posts_balanced_ledger(
    db_session,
    accounts,
    clearing_account,
    pending_payment,
    monkeypatch,
    provider,
    provider_payment_id,
):
    import app.services.payment_verification_service as verification

    order_id = f"order_{provider}_001"

    pending_payment.provider = provider
    pending_payment.provider_order_id = order_id
    db_session.commit()

    if provider == "razorpay":
        monkeypatch.setattr(
            verification,
            "fetch_razorpay_payment",
            lambda payment_id: {
                "id": payment_id,
                "order_id": order_id,
                "amount": 10000,
                "currency": "INR",
                "status": "captured",
            },
        )
    else:
        gateway_payment = GatewayPayment(
            provider=provider,
            provider_payment_id=provider_payment_id,
            provider_order_id=order_id,
            amount_minor=10000,
            currency="INR",
            status="success",
            captured=True,
        )

        class FakeGatewayAdapter:
            def fetch_payment(self, requested_order_id):
                assert requested_order_id == order_id
                return gateway_payment

        monkeypatch.setattr(
            verification,
            "get_gateway_adapter",
            lambda requested_provider: FakeGatewayAdapter(),
        )

    result = recover_and_post_payment(
        db=db_session,
        payment_id=pending_payment.id,
        razorpay_payment_id=(
            provider_payment_id if provider == "razorpay" else None
        ),
    )

    assert result["status"] == "succeeded"
    assert result["already_processed"] is False

    db_session.refresh(pending_payment)

    assert pending_payment.status == "succeeded"
    assert pending_payment.provider_payment_id == provider_payment_id

    transaction = db_session.execute(
        select(LedgerTransaction).where(
            LedgerTransaction.payment_id == pending_payment.id
        )
    ).scalar_one()

    entries = db_session.execute(
        select(LedgerEntry).where(
            LedgerEntry.transaction_id == transaction.id
        )
    ).scalars().all()

    assert len(entries) == 2

    debit = next(entry for entry in entries if entry.side == "debit")
    credit = next(entry for entry in entries if entry.side == "credit")

    assert debit.account_id == clearing_account.id
    assert credit.account_id == accounts["merchant"].id

    assert debit.amount_minor == 10000
    assert credit.amount_minor == 10000

    assert sum(
        entry.amount_minor for entry in entries if entry.side == "debit"
    ) == sum(
        entry.amount_minor for entry in entries if entry.side == "credit"
    )


@pytest.mark.parametrize(
    ("provider", "provider_payment_id"),
    [
        ("razorpay", "pay_razorpay_repeat_001"),
        ("cashfree", "cf_payment_repeat_001"),
        ("payu", "payu_payment_repeat_001"),
    ],
)
def test_repeated_recovery_does_not_duplicate_ledger(
    db_session,
    accounts,
    clearing_account,
    pending_payment,
    monkeypatch,
    provider,
    provider_payment_id,
):
    import app.services.payment_verification_service as verification

    order_id = f"order_{provider}_repeat"

    pending_payment.provider = provider
    pending_payment.provider_order_id = order_id
    db_session.commit()

    if provider == "razorpay":
        monkeypatch.setattr(
            verification,
            "fetch_razorpay_payment",
            lambda payment_id: {
                "id": payment_id,
                "order_id": order_id,
                "amount": 10000,
                "currency": "INR",
                "status": "captured",
            },
        )
    else:
        gateway_payment = GatewayPayment(
            provider=provider,
            provider_payment_id=provider_payment_id,
            provider_order_id=order_id,
            amount_minor=10000,
            currency="INR",
            status="success",
            captured=True,
        )

        class FakeGatewayAdapter:
            def fetch_payment(self, requested_order_id):
                return gateway_payment

        monkeypatch.setattr(
            verification,
            "get_gateway_adapter",
            lambda requested_provider: FakeGatewayAdapter(),
        )

    first_result = recover_and_post_payment(
        db=db_session,
        payment_id=pending_payment.id,
        razorpay_payment_id=(
            provider_payment_id if provider == "razorpay" else None
        ),
    )

    second_result = recover_and_post_payment(
        db=db_session,
        payment_id=pending_payment.id,
        razorpay_payment_id=(
            provider_payment_id if provider == "razorpay" else None
        ),
    )

    assert first_result["already_processed"] is False
    assert second_result["already_processed"] is True

    transaction_count = db_session.scalar(
        select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.payment_id == pending_payment.id
        )
    )

    entry_count = db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    )

    assert transaction_count == 1
    assert entry_count == 2

    db_session.refresh(clearing_account)
    db_session.refresh(accounts["merchant"])

    assert clearing_account.current_balance_minor == 10000
    assert accounts["merchant"].current_balance_minor == 10000


@pytest.mark.parametrize("provider", ["cashfree", "payu"])
def test_recovery_rejects_wrong_gateway_amount(
    db_session,
    clearing_account,
    pending_payment,
    monkeypatch,
    provider,
):
    import app.services.payment_verification_service as verification

    order_id = f"order_{provider}_wrong_amount"

    pending_payment.provider = provider
    pending_payment.provider_order_id = order_id
    db_session.commit()

    gateway_payment = GatewayPayment(
        provider=provider,
        provider_payment_id=f"{provider}_payment_wrong_amount",
        provider_order_id=order_id,
        amount_minor=5000,
        currency="INR",
        status="success",
        captured=True,
    )

    class FakeGatewayAdapter:
        def fetch_payment(self, requested_order_id):
            return gateway_payment

    monkeypatch.setattr(
        verification,
        "get_gateway_adapter",
        lambda requested_provider: FakeGatewayAdapter(),
    )

    with pytest.raises(
        PaymentVerificationError,
        match="amount does not match",
    ):
        recover_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
        )

    assert db_session.scalar(
        select(func.count()).select_from(LedgerTransaction)
    ) == 0

    assert db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    ) == 0


def test_razorpay_recovery_requires_payment_id(
    db_session,
    pending_payment,
):
    with pytest.raises(
        PaymentVerificationError,
        match="Razorpay payment ID is required",
    ):
        recover_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
        )


def test_recovery_rejects_wrong_razorpay_payment_id(
    db_session,
    pending_payment,
    monkeypatch,
):
    import app.services.payment_verification_service as verification

    monkeypatch.setattr(
        verification,
        "fetch_razorpay_payment",
        lambda payment_id: {
            "id": "different_payment_id",
            "order_id": "order_test_001",
            "amount": 10000,
            "currency": "INR",
            "status": "captured",
        },
    )

    with pytest.raises(
        PaymentVerificationError,
        match="different payment ID",
    ):
        recover_and_post_payment(
            db=db_session,
            payment_id=pending_payment.id,
            razorpay_payment_id="pay_expected_001",
        )

    assert db_session.scalar(
        select(func.count()).select_from(LedgerTransaction)
    ) == 0
