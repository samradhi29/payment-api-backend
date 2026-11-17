
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_merchant
from app.core.database import get_db
from app.models.payment import Payment
from app.schemas.payment import PaymentResponse


router = APIRouter(
    prefix="/api/v1/payments",
    tags=["Payments"],
)


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
)
def get_payment_details(
    payment_id: UUID,
    db: Session = Depends(get_db),
    merchant=Depends(get_current_merchant),
):
    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            Payment.merchant_account_id == merchant.id,
        )
        .first()
    )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment
