"""SSRF guard for caller-supplied outbound URLs (docs/security.md §4 SSRF, S-16).

`assert_safe_url` is the single gate every route that fetches a URL a *caller*
controls must pass its input through BEFORE any request. It rejects, with HTTP
400:
  * non-http(s) schemes (ftp://, file://, gopher://, …);
  * a missing hostname;
  * a literal IP for the host — agent endpoints are domain-based; a raw IP can
    never be domain-verified here anyway, and forbidding literals outright kills
    an entire bypass class (IPv4, IPv6, and IPv4-mapped `::ffff:127.0.0.1`
    literals all fail `ipaddress.ip_address`-parses check);
  * a host that resolves (any A/AAAA record) to a private / loopback /
    link-local / reserved / multicast / unspecified address — this is what
    blocks the loopback port-oracle (S-16), the `169.254.169.254` cloud-metadata
    endpoint, and RFC1918 co-tenant scanning on the shared droplet;
  * a port other than 80/443 — agent endpoints are standard web endpoints, and
    pinning the port stops the internal-service port scan (redis 6379, postgres
    5432, uvicorn 8000, hellfire 8090, shos 8200, …) even against a public host.

Residual (documented, not closed here): DNS rebinding — the host is resolved
here and re-resolved by httpx at fetch time, so a TTL-0 record could answer
public now and private on the fetch. Full protection = pinning the resolved IP
onto the httpx transport; a separate task if the owner wants it. Also combine
this guard with `follow_redirects=False` at the call site, so a public URL that
302s to an internal address can't smuggle past a one-time check.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """A v4/v6 address we must never fetch. Unwraps IPv4-mapped IPv6
    (`::ffff:127.0.0.1`) first — the mapped form's own `is_loopback` is False,
    so it must be classified on the embedded v4 address."""
    if ip.version == 6 and getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(raw: str) -> str:
    """Return `raw` unchanged if it is safe to fetch, else raise HTTP 400.

    Pure/synchronous (DNS via getaddrinfo) so it can run inline before opening a
    client. Never leaks *why* beyond a coarse reason — enough for a legitimate
    caller to fix their URL, not a scanning aid."""
    try:
        parsed = urlparse(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid endpoint_url")

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail="endpoint_url must be http or https")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="endpoint_url must have a hostname")

    # Port: default by scheme when absent; only 80/443 allowed.
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail="endpoint_url has an invalid port")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in _ALLOWED_PORTS:
        raise HTTPException(status_code=400, detail="endpoint_url port must be 80 or 443")

    # Literal IP host (v4, v6, or IPv4-mapped) — forbidden outright.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not a literal IP → a hostname, resolve it below
    else:
        raise HTTPException(
            status_code=400, detail="endpoint_url must use a domain name, not an IP address"
        )

    # Resolve the hostname and reject if ANY answer is non-public.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="endpoint_url host does not resolve")
    if not infos:
        raise HTTPException(status_code=400, detail="endpoint_url host does not resolve")
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="endpoint_url host resolves to an invalid address"
            )
        if _is_blocked_ip(ip):
            raise HTTPException(
                status_code=400,
                detail="endpoint_url resolves to a private, loopback, or non-public address",
            )

    return raw
