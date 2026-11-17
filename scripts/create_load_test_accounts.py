
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.account import Account
from app.services.merchant_api_key_service import create_merchant_api_key


def get_or_create_account(
    db,
    account_number: str,
    name: str,
):
    account = db.execute(
        select(Account).where(
            Account.account_number == account_number
        )
    ).scalar_one_or_none()

    if account is not None:
        if account.currency != "INR":
            raise RuntimeError(
                f"{account_number} must use INR"
            )
        return account

    account = Account(
        account_number=account_number,
        name=name,
        type="liability",
        normal_side="credit",
        currency="INR",
        current_balance_minor=0,
        allow_negative=False,
    )

    db.add(account)
    db.flush()

    return account


def main():
    db = SessionLocal()

    try:
        merchant = get_or_create_account(
            db,
            "LOADTEST-MERCHANT-001",
            "LedgerCore Load Test Merchant",
        )

        customer = get_or_create_account(
            db,
            "LOADTEST-CUSTOMER-001",
            "LedgerCore Load Test Customer",
        )

        db.commit()

        _, raw_key = create_merchant_api_key(
            db=db,
            account_id=merchant.id,
            label="LedgerCore Local Load Test",
        )

        print("\nLoad-test accounts ready.")
        print(f"Merchant ID: {merchant.id}")
        print(f"Customer ID: {customer.id}")
        print("\nYour load-test API key (save it securely):")
        print(raw_key)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
