
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.payments import router as payment_router
from app.api.payment_details import router as payment_details_router
from app.api.webhooks import router as webhook_router
from app.api.merchant_profile import router as merchant_profile_router
from app.core.database import get_db


app = FastAPI(
    title="LedgerCore",
    description="Payment Processing and Ledger Management API",
    version="1.0.0",
)


# Register API routers
app.include_router(payment_router)
app.include_router(payment_details_router)
app.include_router(webhook_router)
app.include_router(merchant_profile_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "ledgercore",
    }


@app.get("/health/db", tags=["Health"])
def database_health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
            "service": "ledgercore",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "service": "ledgercore",
            },
        ) from exc


@app.get("/ready", tags=["Health"])
def readiness_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready", "service": "ledgercore"}
