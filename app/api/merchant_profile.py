
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_merchant
from app.core.database import get_db
from app.models.merchant_profile import MerchantProfile
from app.schemas.merchant_profile import (
    MerchantProfileResponse,
    MerchantProfileUpdate,
)


router = APIRouter(
    prefix="/api/v1/merchant/profile",
    tags=["Merchant Profile"],
)


@router.get(
    "",
    response_model=MerchantProfileResponse,
)
def get_merchant_profile(
    merchant=Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    profile = db.get(
        MerchantProfile,
        merchant.id,
    )

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Merchant profile not found",
        )

    return profile


@router.put(
    "",
    response_model=MerchantProfileResponse,
)
def update_merchant_profile(
    payload: MerchantProfileUpdate,
    merchant=Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    profile = db.get(
        MerchantProfile,
        merchant.id,
    )

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Merchant profile not found",
        )

    if not profile.is_active:
        raise HTTPException(
            status_code=403,
            detail="Inactive merchant profiles cannot be updated",
        )

    # The Pydantic schema validates gateway names,
    # preferred gateway membership, and smart-routing requirements.
    profile.enabled_gateways = list(
        dict.fromkeys(payload.enabled_gateways)
    )
    profile.preferred_gateway = payload.preferred_gateway
    profile.smart_routing_enabled = (
        payload.smart_routing_enabled
    )

    try:
        db.commit()
        db.refresh(profile)
    except Exception:
        db.rollback()
        raise

    return profile
