
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = (
        "postgresql+psycopg://ledgercore:YOUR_PASSWORD@localhost:5432/ledgercore"
    )

    # Default payment gateway
    PAYMENT_PROVIDER: str = "razorpay"

    # Razorpay
    RAZORPAY_KEY_ID: str | None = None
    RAZORPAY_KEY_SECRET: str | None = None

    # Cashfree
    CASHFREE_CLIENT_ID: str | None = None
    CASHFREE_CLIENT_SECRET: str | None = None
    CASHFREE_API_VERSION: str = "2026-01-01"
    CASHFREE_ENV: str = "sandbox"

    # PayU
    PAYU_MERCHANT_KEY: str | None = None
    PAYU_MERCHANT_SALT: str | None = None
    PAYU_ENV: str = "sandbox"

    # PhonePe (legacy configuration)
    PHONEPE_CLIENT_ID: str | None = None
    PHONEPE_CLIENT_SECRET: str | None = None
    PHONEPE_CLIENT_VERSION: int = 1
    PHONEPE_ENV: str = "sandbox"

    # Kafka telemetry
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"
    KAFKA_TOPIC: str = "payment.attempt.events"

    # Existing webhook encryption
    WEBHOOK_ENCRYPTION_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
