
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_base_url: str
    razorpay_timeout_seconds: float

    cashfree_client_id: str
    cashfree_client_secret: str
    cashfree_environment: str
    cashfree_api_version: str
    cashfree_timeout_seconds: float

    payu_merchant_key: str
    payu_merchant_salt: str
    payu_environment: str
    payu_success_url: str
    payu_failure_url: str
    payu_timeout_seconds: float


def get_settings() -> Settings:
    return Settings(
        razorpay_key_id=os.getenv("RAZORPAY_KEY_ID", ""),
        razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET", ""),
        razorpay_base_url=os.getenv(
            "RAZORPAY_BASE_URL",
            "https://api.razorpay.com/v1",
        ).rstrip("/"),
        razorpay_timeout_seconds=float(
            os.getenv("RAZORPAY_TIMEOUT_SECONDS", "10")
        ),

        cashfree_client_id=os.getenv("CASHFREE_CLIENT_ID", ""),
        cashfree_client_secret=os.getenv("CASHFREE_CLIENT_SECRET", ""),
        cashfree_environment=os.getenv(
            "CASHFREE_ENV", "sandbox"
        ).strip().lower(),
        cashfree_api_version=os.getenv(
            "CASHFREE_API_VERSION", "2023-08-01"
        ),
        cashfree_timeout_seconds=float(
            os.getenv("CASHFREE_TIMEOUT_SECONDS", "15")
        ),

        payu_merchant_key=os.getenv("PAYU_MERCHANT_KEY", "").strip(),
        payu_merchant_salt=os.getenv("PAYU_MERCHANT_SALT", "").strip(),
        payu_environment=os.getenv(
            "PAYU_ENV", "sandbox"
        ).strip().lower(),
        payu_success_url=os.getenv("PAYU_SUCCESS_URL", "").strip(),
        payu_failure_url=os.getenv("PAYU_FAILURE_URL", "").strip(),
        payu_timeout_seconds=float(
            os.getenv("PAYU_TIMEOUT_SECONDS", "20")
        ),
    )
