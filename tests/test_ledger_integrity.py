from sqlalchemy import func, case
import pytest
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.models.ledger_transaction import LedgerTransaction
from app.models.ledger_entry import LedgerEntry


@pytest.fixture
def ledger_accounts(account_factory):
    clearing = account_factory(
        account_number="TEST-CLEARING-001",
        name="Test Clearing Account",
        account_type="asset",
        normal_side="debit",
        balance=0,
        allow_negative=False,
    )

    merchant = account_factory(
        account_number="TEST-LEDGER-MERCHANT-001",
        name="Test Merchant Payable",
        account_type="liability",
        normal_side="credit",
        balance=0,
        allow_negative=False,
    )

    return {
        "clearing": clearing,
        "merchant": merchant,
    }


def create_balanced_transaction(db_session, ledger_accounts):
    transaction = LedgerTransaction(
        payment_id=None,
        type="adjustment",
        reference_id=uuid4(),
        currency="INR",
        description="Ledger integrity test",
    )

    db_session.add(transaction)
    db_session.flush()

    db_session.add_all(
        [
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=ledger_accounts["clearing"].id,
                side="debit",
                amount_minor=10000,
            ),
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=ledger_accounts["merchant"].id,
                side="credit",
                amount_minor=10000,
            ),
        ]
    )

    db_session.commit()
    db_session.refresh(transaction)

    return transaction


def test_ledger_entries_cannot_be_updated(
    db_session,
    ledger_accounts,
):
    transaction = create_balanced_transaction(
        db_session,
        ledger_accounts,
    )

    entry = (
        db_session.query(LedgerEntry)
        .filter(LedgerEntry.transaction_id == transaction.id)
        .first()
    )

    entry.amount_minor = 20000

    with pytest.raises(SQLAlchemyError):
        db_session.commit()

    db_session.rollback()


def test_ledger_entries_cannot_be_deleted(
    db_session,
    ledger_accounts,
):
    transaction = create_balanced_transaction(
        db_session,
        ledger_accounts,
    )

    entry = (
        db_session.query(LedgerEntry)
        .filter(LedgerEntry.transaction_id == transaction.id)
        .first()
    )

    db_session.delete(entry)

    with pytest.raises(SQLAlchemyError):
        db_session.commit()

    db_session.rollback()


def test_ledger_transactions_cannot_be_updated(
    db_session,
    ledger_accounts,
):
    transaction = create_balanced_transaction(
        db_session,
        ledger_accounts,
    )

    transaction.description = "Modified transaction"

    with pytest.raises(SQLAlchemyError):
        db_session.commit()

    db_session.rollback()


def test_ledger_transactions_cannot_be_deleted(
    db_session,
    ledger_accounts,
):
    transaction = create_balanced_transaction(
        db_session,
        ledger_accounts,
    )

    db_session.delete(transaction)

    with pytest.raises(SQLAlchemyError):
        db_session.commit()

    db_session.rollback()


def test_unbalanced_transaction_is_rejected(
    db_session,
    ledger_accounts,
):
    transaction = LedgerTransaction(
        type="adjustment",
        reference_id=uuid4(),
        currency="INR",
        description="Unbalanced transaction test",
    )

    db_session.add(transaction)
    db_session.flush()

    db_session.add_all(
        [
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=ledger_accounts["clearing"].id,
                side="debit",
                amount_minor=10000,
            ),
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=ledger_accounts["merchant"].id,
                side="credit",
                amount_minor=9000,
            ),
        ]
    )

    with pytest.raises(SQLAlchemyError):
        db_session.commit()

    db_session.rollback()


def test_account_balances_update_from_ledger_entries(
    db_session,
    ledger_accounts,
):
    create_balanced_transaction(
        db_session,
        ledger_accounts,
    )

    db_session.refresh(ledger_accounts["clearing"])
    db_session.refresh(ledger_accounts["merchant"])

    assert ledger_accounts["clearing"].current_balance_minor == 10000
    assert ledger_accounts["merchant"].current_balance_minor == 10000


def test_account_cannot_have_negative_balance(
    db_session,
    account_factory,
):
    asset_account = account_factory(
        account_number="TEST-NEGATIVE-ASSET",
        name="Test Asset Account",
        account_type="asset",
        normal_side="debit",
        balance=0,
        allow_negative=False,
    )

    transaction = LedgerTransaction(
        type="adjustment",
        reference_id=uuid4(),
        currency="INR",
        description="Negative balance test",
    )

    db_session.add(transaction)
    db_session.flush()

    db_session.add(
        LedgerEntry(
            transaction_id=transaction.id,
            account_id=asset_account.id,
            side="credit",
            amount_minor=100,
        )
    )

    with pytest.raises(SQLAlchemyError):
        db_session.flush()

    db_session.rollback()


def test_ledger_entry_currency_must_match_transaction(
    db_session,
    account_factory,
):
    usd_account = account_factory(
        account_number="TEST-USD-ASSET",
        name="Test USD Asset",
        account_type="asset",
        normal_side="debit",
        currency="USD",
        balance=0,
        allow_negative=False,
    )

    transaction = LedgerTransaction(
        type="adjustment",
        reference_id=uuid4(),
        currency="INR",
        description="Currency mismatch test",
    )

    db_session.add(transaction)
    db_session.flush()

    db_session.add(
        LedgerEntry(
            transaction_id=transaction.id,
            account_id=usd_account.id,
            side="debit",
            amount_minor=100,
        )
    )

    with pytest.raises(SQLAlchemyError):
        db_session.flush()

    db_session.rollback()
def test_global_ledger_balance_is_zero_by_currency(db_session):
    """
    Verify that total debits equal total credits
    independently for every currency.
    """

    balance_query = (
        db_session.query(
            LedgerTransaction.currency,
            func.sum(
                case(
                    (LedgerEntry.side == "debit", LedgerEntry.amount_minor),
                    else_=0,
                )
            ).label("total_debits"),
            func.sum(
                case(
                    (LedgerEntry.side == "credit", LedgerEntry.amount_minor),
                    else_=0,
                )
            ).label("total_credits"),
        )
        .join(
            LedgerEntry,
            LedgerEntry.transaction_id == LedgerTransaction.id,
        )
        .group_by(LedgerTransaction.currency)
    )

    balances = balance_query.all()

    for currency, total_debits, total_credits in balances:
        assert total_debits == total_credits, (
            f"Ledger imbalance for {currency}: "
            f"debits={total_debits}, credits={total_credits}"
        )
