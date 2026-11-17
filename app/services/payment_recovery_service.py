
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.payment_verification_service import (
    recover_and_post_payment as _recover_and_post_payment,
)


def recover_and_post_payment(
    db: Session,
    payment_id: UUID,
    razorpay_payment_id: str | None = None,
) -> dict:
    """Delegate all recovery to the canonical verification service."""
    return _recover_and_post_payment(
        db=db,
        payment_id=payment_id,
        razorpay_payment_id=razorpay_payment_id,
    )
