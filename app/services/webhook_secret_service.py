
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _get_fernet() -> Fernet:
    """
    Return a Fernet instance using the configured encryption key.
    Fail explicitly if the key is missing or invalid.
    """
    key = settings.WEBHOOK_ENCRYPTION_KEY

    if not key:
        raise RuntimeError(
            "WEBHOOK_ENCRYPTION_KEY is not configured"
        )

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "WEBHOOK_ENCRYPTION_KEY is invalid"
        ) from exc


def generate_webhook_secret() -> str:
    """
    Generate a cryptographically secure random webhook signing secret.
    The plaintext secret should only be shown to the merchant once.
    """
    return secrets.token_urlsafe(32)


def encrypt_webhook_secret(secret: str) -> str:
    """
    Encrypt a webhook signing secret before storing it in the database.
    """
    if not secret or not secret.strip():
        raise ValueError("Webhook secret cannot be empty")

    encrypted = _get_fernet().encrypt(
        secret.encode("utf-8")
    )

    return encrypted.decode("utf-8")


def decrypt_webhook_secret(encrypted_secret: str) -> str:
    """
    Decrypt a stored webhook signing secret when signing a delivery.
    """
    if not encrypted_secret:
        raise ValueError("Encrypted webhook secret cannot be empty")

    try:
        decrypted = _get_fernet().decrypt(
            encrypted_secret.encode("utf-8")
        )
        return decrypted.decode("utf-8")

    except InvalidToken as exc:
        raise ValueError(
            "Unable to decrypt webhook secret. "
            "Check the encryption key configuration."
        ) from exc
