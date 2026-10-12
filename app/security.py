"""SSRF protection and safe URL handling for any configurable/outbound fetch."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".corp", ".lan")
_SECRET_PARAM = re.compile(r"(apikey|api_key|x-api-key|token|key)=([^&\s'\"]+)", re.I)


class UnsafeURLError(ValueError):
    """Raised when a URL must not be fetched or stored."""


def redact(text: str) -> str:
    """Remove API keys from strings (exception messages often contain full request URLs)."""
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=REDACTED", text or "")


def validate_article_url(url: str) -> bool:
    """Cheap syntactic check for article links we store (no network access)."""
    if not url or len(url) > 2048 or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ALLOWED_SCHEMES and bool(parts.hostname)


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> str:
    """Ensure a URL points to a public host before we fetch it (SSRF guard).

    Rejects non-http(s) schemes, credentials, unusual ports, localhost/internal names,
    and any resolved address that is private, loopback, link-local, multicast or reserved.
    Residual risk: DNS rebinding between this check and the connection (see README).
    """
    if not validate_article_url(url):
        raise UnsafeURLError("URL is malformed or uses a disallowed scheme")
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("non-standard port is not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError("internal hostname is not allowed")
    try:
        infos = resolver(host, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError("host does not resolve") from exc
    if not infos:
        raise UnsafeURLError("host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURLError("host resolves to a non-public address")
    return url
