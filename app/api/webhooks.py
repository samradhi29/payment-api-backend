
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_merchant
from app.core.database import get_db
from app.models.account import Account
from app.schemas.webhook import (
    WebhookEndpointCreate,
    WebhookEndpointCreatedResponse,
    WebhookEndpointResponse,
    WebhookEndpointStatusResponse,
)
from app.services.webhook_service import (
    WebhookServiceError,
    create_webhook_endpoint,
    deactivate_webhook_endpoint,
    list_webhook_endpoints,
)


router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["Webhooks"],
)


@router.post(
    "",
    response_model=WebhookEndpointCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_webhook(
    request: WebhookEndpointCreate,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    try:
        endpoint, secret = create_webhook_endpoint(
            db=db,
            merchant_account_id=merchant.id,
            request=request,
        )

        endpoint_response = WebhookEndpointResponse.model_validate(endpoint)

        return WebhookEndpointCreatedResponse(
            **endpoint_response.model_dump(),
            secret=secret,
        )

    except (WebhookServiceError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "",
    response_model=list[WebhookEndpointResponse],
)
def get_webhook_endpoints(
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    return list_webhook_endpoints(
        db=db,
        merchant_account_id=merchant.id,
    )


@router.delete(
    "/{endpoint_id}",
    response_model=WebhookEndpointStatusResponse,
)
def delete_webhook_endpoint(
    endpoint_id: UUID,
    db: Session = Depends(get_db),
    merchant: Account = Depends(get_current_merchant),
):
    endpoint = deactivate_webhook_endpoint(
        db=db,
        merchant_account_id=merchant.id,
        endpoint_id=endpoint_id,
    )

    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook endpoint not found",
        )

    return WebhookEndpointStatusResponse(
        id=endpoint.id,
        is_active=endpoint.is_active,
        message="Webhook endpoint deactivated successfully",
    )
