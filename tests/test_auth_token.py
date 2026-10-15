"""15.10 — session-JWT contract after the python-jose → PyJWT migration
(docs/api.md "Auth", docs/security.md).

python-jose 3.5.0 (PYSEC-2026-4298) and its `ecdsa` dep (PYSEC-2026-1325) have
no fix version, so `app/core/auth.py` moved to PyJWT. Neither advisory was
reachable in our usage — HS256 with a symmetric secret, accepted algorithm
pinned in code — and these tests lock that shape in so a future change cannot
quietly reintroduce it:

  - round-trip through `create_access_token` / `decode_token` keeps sub+ver
  - the header really is HS256 (not "none", not an asymmetric alg)
  - a token signed with another algorithm, a wrong secret, or no `exp` is
    refused, and `decode_token` raises ValueError (the contract
    `api/app/api/deps.py` catches)

Unit-level like `test_ssrf_guard.py` — no Postgres/Redis.
"""

import base64
import json
import uuid
from datetime import timedelta

import jwt
import pytest

from app.core import auth
from app.core.config import settings


def _header(token: str) -> dict:
    raw = token.split(".")[0]
    raw += "=" * (-len(raw) % 4)
    return json.loads(base64.urlsafe_b64decode(raw))


def test_round_trip_preserves_subject_and_version() -> None:
    sub = str(uuid.uuid4())
    payload = auth.decode_token(auth.create_access_token(sub, token_version=7))
    assert payload["sub"] == sub
    assert payload["ver"] == 7
    assert isinstance(payload["exp"], int)


def test_header_is_pinned_to_hs256() -> None:
    assert _header(auth.create_access_token("x")) == {"alg": "HS256", "typ": "JWT"}


def test_expired_token_is_rejected() -> None:
    token = auth.create_access_token("x", expires_delta=timedelta(minutes=-1))
    with pytest.raises(ValueError):
        auth.decode_token(token)


def test_wrong_secret_is_rejected() -> None:
    # >= 32 bytes: PyJWT warns on short HMAC keys (RFC 7518 3.2), not our point here
    other_secret = "not-the-secret-but-long-enough-for-hs256"
    token = jwt.encode({"sub": "x", "exp": 9999999999, "ver": 0}, other_secret, algorithm="HS256")
    with pytest.raises(ValueError):
        auth.decode_token(token)


def test_alg_none_is_rejected() -> None:
    """The algorithm comes from our code, never from the token header."""
    token = jwt.encode({"sub": "x", "exp": 9999999999, "ver": 0}, None, algorithm="none")
    with pytest.raises(ValueError):
        auth.decode_token(token)


def test_token_without_exp_is_rejected() -> None:
    """A never-expiring token is refused even when correctly signed."""
    token = jwt.encode({"sub": "x", "ver": 0}, settings.secret_key, algorithm="HS256")
    with pytest.raises(ValueError):
        auth.decode_token(token)


@pytest.mark.parametrize("bad", ["", "not-a-token", "a.b.c"])
def test_malformed_tokens_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        auth.decode_token(bad)
