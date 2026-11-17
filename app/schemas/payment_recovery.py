from pydantic import BaseModel, Field


class RazorpayPaymentRecovery(BaseModel):
    razorpay_payment_id: str = Field(
        min_length=1,
        max_length=255,
    )
