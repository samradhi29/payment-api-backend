
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_merchant
from app.core.database import get_db
from app.models.account import Account
from app.schemas.merchant_api_key import (
    MerchantAPIKeyCreate,
    MerchantAPIKeyCreatedResponse,
    MerchantAPIKeyResponse,
)
from app.services.merchant_api_key_service import (
    MerchantAPIKeyError,
    create_merchant_api_key,
    list_merchant_api_keys,
    revoke_merchant_api_key,
)


router = APIRouter(
    prefix="/api/v1/merchant/api-keys",
    tags=["Merchant API Keys"],
)


@router.post(
    "",
    response_model=MerchantAPIKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_key_endpoint(
    payload: MerchantAPIKeyCreate,
    merchant: Account = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    try:
        api_key, raw_secret = create_merchant_api_key(
            db=db,
            account_id=merchant.id,
            label=payload.label,
            expires_at=payload.expires_at,
        )
    except MerchantAPIKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "id": api_key.id,
        "label": api_key.label,
        "key_prefix": api_key.key_prefix,
        "is_active": api_key.is_active,
        "expires_at": api_key.expires_at,
        "created_at": api_key.created_at,
        "secret": raw_secret,
    }


@router.get(
    "",
    response_model=list[MerchantAPIKeyResponse],
)
def list_api_keys_endpoint(
    merchant: Account = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    return list_merchant_api_keys(
        db=db,
        account_id=merchant.id,
    )


@router.delete(
    "/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_api_key_endpoint(
    api_key_id: UUID,
    merchant: Account = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    revoked = revoke_merchant_api_key(
        db=db,
        account_id=merchant.id,
        api_key_id=api_key_id,
    )

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    return None
