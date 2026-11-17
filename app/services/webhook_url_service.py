import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


class WebhookURLValidationError(ValueError):
    """Raised when a webhook destination is unsafe or invalid."""


def _is_public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global


def _resolve_public_addresses(hostname: str) -> list[str]:
    """Resolve a hostname and reject the entire answer if any IP is non-public."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if not literal.is_global:
            raise WebhookURLValidationError(
                "Private or reserved IP addresses are not allowed"
            )
        return [str(literal)]

    try:
        results = socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise WebhookURLValidationError(
            "Webhook hostname could not be resolved"
        ) from exc

    addresses = list(dict.fromkeys(result[4][0].split("%", 1)[0] for result in results))
    if not addresses:
        raise WebhookURLValidationError(
            "Webhook hostname has no valid addresses"
        )

    for address in addresses:
        if not _is_public_ip(address):
            raise WebhookURLValidationError(
                "Webhook hostname resolves to a non-public IP address"
            )

    return addresses


def validate_webhook_url(url: str) -> str:
    """Validate and normalize an HTTPS webhook URL."""
    if not url or not isinstance(url, str):
        raise WebhookURLValidationError("Webhook URL is required")

    url = url.strip()
    if len(url) > 2048:
        raise WebhookURLValidationError("Webhook URL is too long")

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise WebhookURLValidationError("Invalid webhook URL") from exc

    if parsed.scheme.lower() != "https":
        raise WebhookURLValidationError("Webhook URL must use HTTPS")
    if not hostname:
        raise WebhookURLValidationError("Webhook URL must contain a hostname")
    if parsed.username or parsed.password:
        raise WebhookURLValidationError("Webhook URL must not contain credentials")
    if parsed.fragment:
        raise WebhookURLValidationError("Webhook URL must not contain a fragment")
    if port not in (None, 443):
        raise WebhookURLValidationError("Only the default HTTPS port (443) is allowed")

    hostname = hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise WebhookURLValidationError("Invalid webhook hostname") from exc

    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise WebhookURLValidationError("Localhost webhook destinations are not allowed")
    if hostname.endswith(".local"):
        raise WebhookURLValidationError("Local network hostnames are not allowed")

    _resolve_public_addresses(hostname)

    # Normalize only the host. Preserve path, query, and explicit HTTPS scheme.
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port:
        netloc = f"{netloc}:443"
    return parsed._replace(scheme="https", netloc=netloc).geturl()


class PinnedPublicHTTPTransport(httpx.HTTPTransport):
    """Connect to a vetted IP while preserving the original Host header and TLS SNI.

    The destination is resolved immediately before the request. The actual
    socket connection then targets that literal IP, so a second DNS lookup
    cannot redirect the connection to a private address.
    """

    def __init__(self, url: str):
        normalized_url = validate_webhook_url(url)
        parsed = urlsplit(normalized_url)
        self._hostname = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        self._addresses = _resolve_public_addresses(self._hostname)
        self._pinned_ip = self._addresses[0]
        self._is_ip_literal = False
        try:
            ipaddress.ip_address(self._hostname)
            self._is_ip_literal = True
        except ValueError:
            pass

        super().__init__(verify=True)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request_host = request.url.host.rstrip(".").lower()
        if request_host != self._hostname:
            raise WebhookURLValidationError(
                "Pinned webhook transport cannot be reused for another hostname"
            )

        host_header = (
            f"[{self._hostname}]"
            if ":" in self._hostname
            else self._hostname
        )
        request.headers["Host"] = host_header
        request.url = request.url.copy_with(host=self._pinned_ip)

        if not self._is_ip_literal:
            request.extensions["sni_hostname"] = self._hostname.encode("ascii")

        return super().handle_request(request)
