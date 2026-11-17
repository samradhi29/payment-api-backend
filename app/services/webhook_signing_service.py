
import hashlib
import hmac
import json
import time
from typing import Any


def serialize_webhook_payload(payload: dict[str, Any]) -> bytes:
    """
    Serialize the webhook payload deterministically.

    The exact bytes returned here must be used both for signing
    and for sending the HTTP request.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def generate_webhook_signature(
    secret: str,
    payload: bytes,
    timestamp: int | None = None,
) -> tuple[str, int]:
    """
    Generate an HMAC-SHA256 signature.

    Signed message format:
        timestamp.payload

    Returns:
        (signature, timestamp)
    """
    if not secret:
        raise ValueError("Webhook signing secret is required")

    if not isinstance(payload, bytes):
        raise TypeError("Webhook payload must be bytes")

    if timestamp is None:
        timestamp = int(time.time())

    signed_content = (
        str(timestamp).encode("utf-8")
        + b"."
        + payload
    )

    signature = hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()

    return signature, timestamp


def build_webhook_headers(
    secret: str,
    payload: bytes,
    timestamp: int | None = None,
) -> dict[str, str]:
    """
    Build HTTP headers for a signed webhook request.
    """
    signature, timestamp = generate_webhook_signature(
        secret=secret,
        payload=payload,
        timestamp=timestamp,
    )

    return {
        "Content-Type": "application/json",
        "User-Agent": "LedgerCore-Webhooks/1.0",
        "X-LedgerCore-Timestamp": str(timestamp),
        "X-LedgerCore-Signature": f"sha256={signature}",
    }


def verify_webhook_signature(
    secret: str,
    payload: bytes,
    signature_header: str,
    timestamp: int,
    tolerance_seconds: int = 300,
    current_time: int | None = None,
) -> bool:
    """
    Verify a LedgerCore webhook signature.

    Rejects timestamps outside the configured tolerance to reduce
    replay attacks. Merchants should additionally store processed
    event IDs to prevent duplicate processing.
    """
    if not secret or not signature_header:
        return False

    if tolerance_seconds < 0:
        return False

    if current_time is None:
        current_time = int(time.time())

    if abs(current_time - timestamp) > tolerance_seconds:
        return False

    expected_signature, _ = generate_webhook_signature(
        secret=secret,
        payload=payload,
        timestamp=timestamp,
    )

    expected_header = f"sha256={expected_signature}"

    return hmac.compare_digest(
        expected_header,
        signature_header,
    )
