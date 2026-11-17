
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_merchant
from app.core.database import get_db
from app.models.account import Account
from app.models.payment import Payment
from app.providers.payu import PayUAdapter
from app.schemas.payment import (
    PaymentCreate,
    PaymentResponse,
    RazorpayPaymentVerification,
)
from app.schemas.payment_recovery import RazorpayPaymentRecovery
from app.services.payment_service import (
    create_payment,
    IdempotencyConflictError,
)
from app.services.routing_service import RoutingUnavailableError
from app.services.merchant_profile_service import MerchantProfileError
from app.services.gateway_service import (
    create_gateway_order,
    GatewayServiceError,
)
from app.services.payment_verification_service import (
    verify_and_post_payment,
    recover_and_post_payment,
    PaymentVerificationError,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["Payments"],
)


def _provider_value(payment: Payment) -> str:
    provider = payment.provider
    return (
        provider.value if hasattr(provider, "value") else provider
    ).lower()


def get_owned_payment(
    db: Session,
    payment_id: UUID,
    merchant_id: UUID,
) -> Payment:
    payment = db.get(Payment, payment_id)

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    if payment.merchant_account_id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    return payment


def _verify_payu_callback_hash(data: dict[str, str]) -> bool:
    try:
        adapter = PayUAdapter()
        return adapter.verify_response_hash(data)
    except Exception:
        logger.exception("PayU callback hash verification failed")
        return False


async def _read_payu_callback(request: Request) -> dict[str, str]:
    try:
        body = await request.body()
        decoded_body = body.decode("utf-8")
        parsed = parse_qs(
            decoded_body,
            keep_blank_values=True,
            strict_parsing=False,
        )

        result: dict[str, str] = {}

        for key, values in parsed.items():
            if len(values) != 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid duplicate PayU callback field: {key}",
                )

            result[key] = values[0]

        return result

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayU callback encoding",
        )


def _find_payu_payment(
    db: Session,
    txnid: str,
) -> Payment:
    statement = select(Payment).where(
        Payment.provider == "payu",
        Payment.provider_order_id == txnid,
    )

    payment = db.scalar(statement)

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PayU payment not found",
        )

    return payment


def _validate_payu_callback_amount(
    payment: Payment,
    callback_amount: str,
) -> None:
    try:
        amount_minor = Decimal(callback_amount) * Decimal("100")
    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayU callback amount",
        )

    if (
        not amount_minor.is_finite()
        or amount_minor != amount_minor.to_integral_value()
        or int(amount_minor) != payment.amount_minor
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PayU callback amount does not match payment amount",
        )


async def _handle_payu_callback(
    request: Request,
    db: Session,
):
    data = await _read_payu_callback(request)

    if not _verify_payu_callback_hash(data):
        logger.warning("PayU callback hash validation failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayU callback hash",
        )

    required_fields = {"txnid", "amount", "status"}

    if not required_fields.issubset(data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PayU callback is missing required fields",
        )

    payment = _find_payu_payment(
        db=db,
        txnid=data["txnid"],
    )

    if _provider_value(payment) != "payu":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment provider mismatch",
        )

    _validate_payu_callback_amount(
        payment=payment,
        callback_amount=data["amount"],
    )

    callback_status = data["status"].strip().lower()

    if callback_status != "success":
        logger.info(
            "PayU reported a non-successful callback for payment_id=%s",
            payment.id,
        )

        return {
            "status": "not_successful",
            "payment_id": str(payment.id),
            "message": (
                "PayU did not report a successful payment. "
                "The payment remains available for reconciliation."
            ),
        }

    try:
        result = verify_and_post_payment(
            db=db,
            payment_id=payment.id,
        )

        logger.info(
            "PayU payment verified successfully: payment_id=%s",
            payment.id,
        )

        return result

    except PaymentVerificationError as exc:
        logger.warning(
            "PayU server-side verification failed for payment_id=%s: %s",
            payment.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/test")
def test_payment_router():
    return {"message": "Payment router is working!"}


@router.get(
    "",
    response_model=list[PaymentResponse],
)
def list_payments_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    statement = (
        select(Payment)
        .where(Payment.merchant_account_id == merchant.id)
        .order_by(
            Payment.created_at.desc(),
            Payment.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    return db.scalars(statement).all()


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_endpoint(
    payment_data: PaymentCreate,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    try:
        logger.info(
            "Payment request received: provider=%s merchant_id=%s",
            payment_data.provider,
            merchant.id,
        )

        payment = create_payment(
            db=db,
            merchant_account_id=merchant.id,
            payment_data=payment_data,
            idempotency_key=idempotency_key,
        )

        logger.info(
            "Payment created: payment_id=%s provider=%s",
            payment.id,
            payment.provider,
        )

        return payment

    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    except MerchantProfileError as exc:
        logger.warning(
            "Merchant profile rejected payment for merchant_id=%s: %s",
            merchant.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    except RoutingUnavailableError as exc:
        logger.warning(
            "Automatic gateway routing unavailable: %s",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    except Exception:
        logger.exception("Failed to create payment")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment",
        )


@router.post("/{payment_id}/gateway-order")
def create_gateway_order_endpoint(
    payment_id: UUID,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    get_owned_payment(
        db=db,
        payment_id=payment_id,
        merchant_id=merchant.id,
    )

    try:
        return create_gateway_order(
            db=db,
            payment_id=payment_id,
        )
    except GatewayServiceError as exc:
        message = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if message == "Payment not found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=error_status,
            detail=message,
        )
    except Exception:
        logger.exception("Failed to create gateway order")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create gateway order",
        )


@router.post("/{payment_id}/verify")
def verify_payment_endpoint(
    payment_id: UUID,
    verification_data: RazorpayPaymentVerification | None = None,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    payment = get_owned_payment(
        db=db,
        payment_id=payment_id,
        merchant_id=merchant.id,
    )

    provider = _provider_value(payment)

    try:
        if provider == "razorpay":
            if verification_data is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Razorpay verification details are required.",
                )

            return verify_and_post_payment(
                db=db,
                payment_id=payment_id,
                razorpay_order_id=verification_data.razorpay_order_id,
                razorpay_payment_id=verification_data.razorpay_payment_id,
                razorpay_signature=verification_data.razorpay_signature,
            )

        if provider in ("cashfree", "payu"):
            return verify_and_post_payment(
                db=db,
                payment_id=payment_id,
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Verification is not supported for provider '{provider}'.",
        )

    except PaymentVerificationError as exc:
        message = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if message == "Payment not found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=error_status,
            detail=message,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to verify payment")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify payment",
        )


@router.post("/{payment_id}/recover")
def recover_payment_endpoint(
    payment_id: UUID,
    recovery_data: RazorpayPaymentRecovery | None = None,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    payment = get_owned_payment(
        db=db,
        payment_id=payment_id,
        merchant_id=merchant.id,
    )

    provider = _provider_value(payment)

    try:
        if provider == "razorpay":
            if recovery_data is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Razorpay recovery details are required.",
                )

            return recover_and_post_payment(
                db=db,
                payment_id=payment_id,
                razorpay_payment_id=recovery_data.razorpay_payment_id,
            )

        if provider in ("cashfree", "payu"):
            return recover_and_post_payment(
                db=db,
                payment_id=payment_id,
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Recovery is not supported for provider '{provider}'.",
        )

    except PaymentVerificationError as exc:
        message = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if message == "Payment not found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=error_status,
            detail=message,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to recover payment")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to recover payment",
        )


@router.post("/payu/success")
async def payu_success_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    return await _handle_payu_callback(
        request=request,
        db=db,
    )


@router.post("/payu/failure")
async def payu_failure_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    return await _handle_payu_callback(
        request=request,
        db=db,
    )
