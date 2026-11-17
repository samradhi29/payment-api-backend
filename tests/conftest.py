
import os

# Force pytest to use the dedicated test database. This must happen BEFORE
# importing app.core.database. The default hostname works inside Compose;
# TEST_DATABASE_URL can point to localhost:5433 for host-run pytest.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://ledgercore_test:"
    "ledgercore_test_password@test-db:5432/ledgercore_test",
)

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal, engine, get_db
from app.main import app
from app.models.account import Account
from app.services.merchant_api_key_service import (
    create_merchant_api_key,
)


def verify_test_database():
    """
    Prevent tests from ever running against the development database.
    """
    database_name = engine.url.database
    database_host = engine.url.host

    if database_name != "ledgercore_test":
        raise RuntimeError(
            f"Unsafe test database: {database_name}. "
            "Expected ledgercore_test."
        )

    database_port = engine.url.port
    safe_compose_target = (
        database_host == "test-db"
        and database_port in (None, 5432)
    )
    safe_host_target = (
        database_host in {"localhost", "127.0.0.1"}
        and database_port == 5433
    )

    if not (safe_compose_target or safe_host_target):
        raise RuntimeError(
            "Unsafe test database target: "
            f"{database_host}:{database_port}. Expected test-db:5432 "
            "or localhost:5433."
        )


@pytest.fixture
def db_session():
    """
    Provides a clean session connected only to the dedicated test database.
    """
    verify_test_database()

    db = SessionLocal()

    try:
        db.execute(
            text(
                """
                TRUNCATE TABLE
                    webhook_deliveries,
                    webhook_endpoints,
                    merchant_profiles,
                    merchant_api_keys,
                    outbox_events,
                    idempotency_keys,
                    ledger_entries,
                    ledger_transactions,
                    payments,
                    accounts
                CASCADE
                """
            )
        )
        db.commit()

        yield db

    finally:
        db.rollback()

        db.execute(
            text(
                """
                TRUNCATE TABLE
                    webhook_deliveries,
                    webhook_endpoints,
                    merchant_profiles,
                    merchant_api_keys,
                    outbox_events,
                    idempotency_keys,
                    ledger_entries,
                    ledger_transactions,
                    payments,
                    accounts
                CASCADE
                """
            )
        )
        db.commit()
        db.close()


@pytest.fixture
def account_factory(db_session):
    """
    Creates reusable test accounts.
    """

    def create_account(
        account_number: str,
        name: str,
        account_type: str = "liability",
        normal_side: str = "credit",
        currency: str = "INR",
        balance: int = 0,
        allow_negative: bool = False,
    ):
        account = Account(
            account_number=account_number,
            name=name,
            type=account_type,
            normal_side=normal_side,
            currency=currency,
            current_balance_minor=balance,
            allow_negative=allow_negative,
        )

        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)

        return account

    return create_account


@pytest.fixture
def accounts(account_factory):
    """
    Creates a customer and merchant account.
    """

    customer = account_factory(
        account_number="TEST-CUSTOMER-001",
        name="Test Customer",
    )

    merchant = account_factory(
        account_number="TEST-MERCHANT-001",
        name="Test Merchant",
    )

    return {
        "customer": customer,
        "merchant": merchant,
    }


@pytest.fixture
def merchant_api_key(accounts, db_session):
    """
    Creates a valid API key for the test merchant.
    Only its hash is stored in PostgreSQL.
    """
    _, raw_key = create_merchant_api_key(
        db=db_session,
        account_id=accounts["merchant"].id,
        label="Pytest Merchant Key",
    )

    return raw_key


@pytest.fixture
def client(merchant_api_key):
    """
    FastAPI test client authenticated as the test merchant.
    """

    verify_test_database()

    def override_get_db():
        db = SessionLocal()

        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            test_client.headers.update(
                {
                    "X-Merchant-API-Key": merchant_api_key,
                }
            )

            yield test_client

    finally:
        app.dependency_overrides.clear()
