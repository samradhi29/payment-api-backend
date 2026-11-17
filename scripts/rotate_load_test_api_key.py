
from uuid import UUID

from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.models.merchant_api_key import MerchantAPIKey
from app.services.merchant_api_key_service import create_merchant_api_key


MERCHANT_ID = UUID("eb47a2b9-ea74-49c1-8062-10bab3a31424")


def main():
    db = SessionLocal()

    try:
        # Revoke all active keys belonging to this load-test merchant.
        result = db.execute(
            update(MerchantAPIKey)
            .where(
                MerchantAPIKey.account_id == MERCHANT_ID,
                MerchantAPIKey.is_active.is_(True),
            )
            .values(is_active=False)
        )

        db.commit()

        print(f"Revoked {result.rowcount} existing load-test API key(s).")

        # Generate one replacement key.
        _, raw_key = create_merchant_api_key(
            db=db,
            account_id=MERCHANT_ID,
            label="LedgerCore Local Load Test - Rotated",
        )

        print("\nNew load-test API key:")
        print(raw_key)
        print("\nSave this key securely. It will not be displayed again.")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
