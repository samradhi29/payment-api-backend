
import argparse
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.database import SessionLocal
from app.services.merchant_api_key_service import (
    create_merchant_api_key,
    MerchantAPIKeyError,
)


def main():
    parser = argparse.ArgumentParser(
        description="Issue a LedgerCore merchant API key"
    )

    parser.add_argument(
        "--account-id",
        required=True,
        help="Existing merchant account UUID",
    )

    parser.add_argument(
        "--label",
        required=True,
        help="A descriptive name for this API key",
    )

    parser.add_argument(
        "--expires-in-days",
        type=int,
        default=None,
        help="Optional key expiry in days",
    )

    args = parser.parse_args()

    if args.expires_in_days is not None and args.expires_in_days <= 0:
        parser.error("--expires-in-days must be greater than zero")

    try:
        account_id = UUID(args.account_id)
    except ValueError:
        parser.error("--account-id must be a valid UUID")

    expires_at = None

    if args.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=args.expires_in_days
        )

    db = SessionLocal()

    try:
        api_key_record, raw_key = create_merchant_api_key(
            db=db,
            account_id=account_id,
            label=args.label,
            expires_at=expires_at,
        )

        print("\nMerchant API key created successfully.")
        print(f"Key record ID: {api_key_record.id}")
        print(f"Account ID: {api_key_record.account_id}")
        print(f"Label: {api_key_record.label}")
        print(f"Key prefix: {api_key_record.key_prefix}")
        print("\nCOPY THIS KEY NOW. IT WILL NOT BE SHOWN AGAIN:\n")
        print(raw_key)
        print()

    except MerchantAPIKeyError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)

    finally:
        db.close()


if __name__ == "__main__":
    main()
