
import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.merchant_api_key import MerchantAPIKey
from app.models.merchant_profile import MerchantProfile


class MerchantAPIKeyError(Exception):
    """Raised when merchant API-key operations are invalid."""


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(
        api_key.encode("utf-8")
    ).hexdigest()

def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def create_merchant_api_key(
    db: Session,
    account_id: UUID,
    label: str,
    expires_at: datetime | None = None,
) -> tuple[MerchantAPIKey, str]:
    normalized_label = label.strip() if label else ""

    if not normalized_label:
        raise MerchantAPIKeyError("API key label is required")

    if len(normalized_label) > 100:
        raise MerchantAPIKeyError("API key label is too long")

    account = db.get(Account, account_id)

    if account is None:
        raise MerchantAPIKeyError("Account not found")

    if expires_at is not None:
        expires_at = _normalize_datetime(expires_at)

        if expires_at <= datetime.now(timezone.utc):
            raise MerchantAPIKeyError(
                "Expiration must be in the future"
            )

    raw_key = f"lc_live_{secrets.token_urlsafe(32)}"

    api_key = MerchantAPIKey(
        account_id=account_id,
        label=normalized_label,
        key_prefix=raw_key[:16],
        key_hash=hash_api_key(raw_key),
        is_active=True,
        expires_at=expires_at,
    )

    try:
        profile = db.get(MerchantProfile, account_id)

        if profile is None:
            profile = MerchantProfile(
                account_id=account_id,
                enabled_gateways=["razorpay"],
                preferred_gateway="razorpay",
                smart_routing_enabled=False,
                is_active=True,
            )
            db.add(profile)

        elif not profile.is_active:
            raise MerchantAPIKeyError(
                "Cannot create an API key for an inactive merchant"
            )

        db.add(api_key)
        db.commit()
        db.refresh(api_key)

    except Exception:
        db.rollback()
        raise

    # The plaintext secret is returned only once.
    return api_key, raw_key


def list_merchant_api_keys(
    db: Session,
    account_id: UUID,
) -> list[MerchantAPIKey]:
    statement = (
        select(MerchantAPIKey)
        .where(MerchantAPIKey.account_id == account_id)
        .order_by(MerchantAPIKey.created_at.desc())
    )

    return list(db.scalars(statement).all())


def revoke_merchant_api_key(
    db: Session,
    account_id: UUID,
    api_key_id: UUID,
) -> bool:
    statement = select(MerchantAPIKey).where(
        MerchantAPIKey.id == api_key_id,
        MerchantAPIKey.account_id == account_id,
    )

    api_key = db.execute(statement).scalar_one_or_none()

    if api_key is None:
        return False

    if not api_key.is_active:
        return True

    try:
        api_key.is_active = False
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def authenticate_merchant_api_key(
    db: Session,
    raw_key: str,
) -> Account | None:
    if not raw_key:
        return None

    key_hash = hash_api_key(raw_key)
    now = datetime.now(timezone.utc)

    statement = (
        select(Account)
        .join(
            MerchantAPIKey,
            MerchantAPIKey.account_id == Account.id,
        )
        .join(
            MerchantProfile,
            MerchantProfile.account_id == Account.id,
        )
        .where(
            MerchantAPIKey.key_hash == key_hash,
            MerchantAPIKey.is_active.is_(True),
            (
                MerchantAPIKey.expires_at.is_(None)
                | (MerchantAPIKey.expires_at > now)
            ),
            MerchantProfile.is_active.is_(True),
        )
    )

    return db.execute(statement).scalar_one_or_none()
