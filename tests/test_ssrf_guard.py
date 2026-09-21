"""15.5 — SSRF guard for `POST /verify-endpoint` (docs/security.md S-16).

Unit tests for `app.core.ssrf.assert_safe_url`: the gate that stops a
caller-supplied `endpoint_url` from making the server fetch loopback / RFC1918 /
link-local / metadata addresses (the live loopback port-oracle reachable
anonymously via MCP since 2026-09-12).

DNS is patched so the suite is deterministic and never leaves the runner. The
literal-IP / scheme / port cases are rejected *before* any resolution, so they
need no patch; only the hostname cases (one public, one DNS-rebinding-style
private-resolving) patch `getaddrinfo`.
"""

import socket

import pytest
from fastapi import HTTPException

from app.core import ssrf


def _patch_resolve(
    monkeypatch: pytest.MonkeyPatch, addr: str, family: int = socket.AF_INET
) -> None:
    def fake_getaddrinfo(host, port, *a, **k):  # noqa: ANN001, ANN002, ANN003
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, port or 0))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)


# ── must be REJECTED (400) ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",      # loopback literal (the live oracle)
        "http://127.0.0.1:1/",               # loopback literal, closed port
        "https://10.0.0.5/",                 # RFC1918 literal
        "http://192.168.1.1/",               # RFC1918 literal
        "http://172.16.0.1/",                # RFC1918 literal
        "http://169.254.169.254/",           # cloud metadata literal
        "http://[::1]/",                     # IPv6 loopback literal
        "http://[fd00::1]/",                 # IPv6 ULA (fc00::/7) literal
        "http://[fe80::1]/",                 # IPv6 link-local literal
        "http://[::ffff:127.0.0.1]/",        # IPv4-mapped IPv6 loopback literal
        "http://[::ffff:10.0.0.1]/",         # IPv4-mapped IPv6 RFC1918 literal
        "http://8.8.8.8/",                   # public literal — still rejected (no literals)
        "ftp://example.com/",                # non-http scheme
        "file:///etc/passwd",                # file scheme
        "gopher://example.com/",             # gopher scheme
        "http://example.com:6379/",          # non-80/443 port (redis oracle)
        "http://example.com:5432/",          # non-80/443 port (postgres oracle)
        "http://example.com:8200/",          # non-80/443 port (shos co-tenant)
        "https://example.com:8443/",         # non-80/443 port
        "not-a-url",                         # no scheme/host
        "http:///nohost",                    # empty host
    ],
)
def test_rejects_unsafe(url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        ssrf.assert_safe_url(url)
    assert exc.value.status_code == 400


def test_rejects_domain_resolving_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS-rebinding-flavoured: a domain that resolves to 127.0.0.1 is blocked
    at resolution time even though the host string looks innocuous."""
    _patch_resolve(monkeypatch, "127.0.0.1")
    with pytest.raises(HTTPException) as exc:
        ssrf.assert_safe_url("https://evil.example.com/")
    assert exc.value.status_code == 400


def test_rejects_domain_resolving_to_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(HTTPException):
        ssrf.assert_safe_url("https://rebind.example.com/")


def test_rejects_domain_resolving_to_ipv4_mapped_private(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, "::ffff:10.0.0.1", family=socket.AF_INET6)
    with pytest.raises(HTTPException):
        ssrf.assert_safe_url("https://rebind6.example.com/")


def test_rejects_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise socket.gaierror("no such host")

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", boom)
    with pytest.raises(HTTPException) as exc:
        ssrf.assert_safe_url("https://does-not-exist.example.com/")
    assert exc.value.status_code == 400


# ── must be ACCEPTED ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "https://agent.example.com/.well-known/agent.json",
        "https://example.com/",
        "http://example.com/",                    # explicit :80 implied
        "https://example.com:443/api",            # explicit standard port
        "http://example.com:80/api",
    ],
)
def test_accepts_public_domain(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _patch_resolve(monkeypatch, "93.184.216.34")  # a public address
    assert ssrf.assert_safe_url(url) == url
