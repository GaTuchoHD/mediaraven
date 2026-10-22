"""Network-boundary validation for user supplied URLs."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit


class UnsafeURLError(ValueError):
    pass


logger = logging.getLogger(__name__)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_SAFE_REDIRECTS = 5


def _validate_ip(address: str) -> None:
    ip = ipaddress.ip_address(address)
    if not ip.is_global:
        raise UnsafeURLError(f"endereço não público: {ip.compressed}")


def validate_url_sync(url: str) -> str:
    """Return a normalized public HTTP(S) URL or raise UnsafeURLError."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("protocolo não permitido")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credenciais na URL não são permitidas")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("host ausente")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        _validate_ip(literal_ip.compressed)
        return url

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise UnsafeURLError("DNS não resolvido") from exc
    if not addresses:
        raise UnsafeURLError("DNS sem endereços")
    for address in addresses:
        _validate_ip(address)
    return url


async def validate_url(url: str) -> str:
    return await asyncio.to_thread(validate_url_sync, url)


def validated_redirect(current_url: str, location: str) -> str:
    target = urljoin(current_url, location)
    return validate_url_sync(target)


@asynccontextmanager
async def safe_aiohttp_get(session, url: str, *, max_redirects: int = _MAX_SAFE_REDIRECTS, **kwargs):
    """GET with every redirect target validated before aiohttp can request it."""
    kwargs.pop("allow_redirects", None)
    current_url = await validate_url(url)

    for _ in range(max_redirects + 1):
        location = None
        async with session.get(current_url, allow_redirects=False, **kwargs) as response:
            if response.status not in _REDIRECT_STATUSES:
                yield response
                return
            location = response.headers.get("Location")
            if not location:
                yield response
                return

        current_url = await asyncio.to_thread(validated_redirect, current_url, location)

    raise UnsafeURLError("redirecionamentos em excesso")


def safe_curl_get(get, url: str, *args, max_redirects: int = _MAX_SAFE_REDIRECTS, **kwargs):
    """curl_cffi/requests-compatible GET that validates redirects hop by hop."""
    kwargs.pop("allow_redirects", None)
    current_url = validate_url_sync(url)

    for _ in range(max_redirects + 1):
        response = get(current_url, *args, allow_redirects=False, **kwargs)
        if response.status_code not in _REDIRECT_STATUSES:
            return response

        location = response.headers.get("Location")
        if not location:
            return response
        close = getattr(response, "close", None)
        if close:
            close()
        current_url = validated_redirect(current_url, location)

    raise UnsafeURLError("redirecionamentos em excesso")


async def safe_playwright_get(
    request_context, url: str, *, max_redirects: int = _MAX_SAFE_REDIRECTS, **kwargs,
):
    """Playwright API GET with redirects disabled and checked before each hop."""
    kwargs.pop("max_redirects", None)
    current_url = await validate_url(url)

    for _ in range(max_redirects + 1):
        response = await request_context.get(current_url, max_redirects=0, **kwargs)
        if response.status not in _REDIRECT_STATUSES:
            return response
        location = response.headers.get("location") or response.headers.get("Location")
        if not location:
            return response
        current_url = await asyncio.to_thread(validated_redirect, current_url, location)

    raise UnsafeURLError("redirecionamentos em excesso")


async def install_public_navigation_guard(page) -> None:
    """Abort Playwright HTTP requests whose URL/redirect target is not public."""
    validated_hosts: set[str] = set()

    async def guard(route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.scheme.lower() in {"about", "data", "blob"}:
            await route.continue_()
            return
        if parsed.scheme.lower() not in {"http", "https"}:
            logger.warning(
                "Playwright bloqueou requisição com protocolo não permitido (%s)",
                parsed.scheme.lower() or "ausente",
            )
            await route.abort("blockedbyclient")
            return

        host = (parsed.hostname or "").casefold()
        try:
            if host not in validated_hosts:
                await validate_url(request.url)
                validated_hosts.add(host)
        except Exception as exc:
            logger.warning(
                "Playwright bloqueou requisição para destino não público (%s)",
                type(exc).__name__,
            )
            await route.abort("blockedbyclient")
            return

        await route.continue_()

    await page.route("**/*", guard)
