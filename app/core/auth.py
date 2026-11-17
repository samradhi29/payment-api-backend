
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.account import Account
from app.services.merchant_api_key_service import (
    authenticate_merchant_api_key,
)


def get_current_merchant(
    x_merchant_api_key: str = Header(
        ...,
        alias="X-Merchant-API-Key",
        min_length=20,
        max_length=200,
    ),
    db: Session = Depends(get_db),
) -> Account:
    merchant = authenticate_merchant_api_key(
        db=db,
        raw_key=x_merchant_api_key,
    )

    if merchant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired merchant API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return merchant
