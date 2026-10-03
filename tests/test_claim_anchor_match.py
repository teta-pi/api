"""1.27 — a domain claim must prove the entity's *anchor* (docs/security.md S-22).

Regression for the pre-verified-entity takeover found in 15.7: the claim flow
(`POST /businesses/{id}/claim/domain/{start,check}`) passed a caller-supplied
`payload.domain` straight to `domain_ownership` and, on success, transferred
`owner_id`. Nothing tied that domain to the anchor `admin.py::
bulk_preverify_entities` recorded, so proving a throwaway domain you own
claimed any `pre_verified_unclaimed` profile.

Lives here, not in infra's `scripts/security/probe.py`, for the same reason
test_blocks_is_public.py does: the probe is read-only by design (security.md
§6.2) and the interesting paths need entity state. The probe gets the
complementary black-box assert (anchor-mismatch claim on a prod fixture → 403).

Unit-level: the routes are called directly with a stub session, the
claim_status gate satisfied by a stub Business, and the DNS/file proof itself
patched to "verified" — so a test that reaches the proof at all would *pass*
the claim, and only the anchor check can produce the 403.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import businesses as biz
from app.services.verification.domain_ownership import anchor_domain, normalize_domain


class _StubBusiness:
    """Just enough Business surface for the claim routes."""

    def __init__(self, source: dict | None) -> None:
        self.id = uuid.uuid4()
        self.owner_id = uuid.uuid4()
        self.claim_status = "pre_verified_unclaimed"
        self.pre_verified_source = source
        self.verification_level = "none"


class _StubSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass


class _StubUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


def _payload(domain: str) -> biz.DomainVerifyRequest:
    return biz.DomainVerifyRequest(domain=domain)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """Anchor = example-anchor.test; the proof always succeeds; token minting
    is stubbed so no Redis is needed."""
    business = _StubBusiness({"domain": "example-anchor.test", "opt_out_token": "x"})
    monkeypatch.setattr(biz, "_get_claimable_business", AsyncMock(return_value=business))
    monkeypatch.setattr(biz, "_compute_verification_level", AsyncMock(return_value="domain"))
    monkeypatch.setattr(
        biz.domain_ownership, "check_domain_verification", AsyncMock(return_value=(True, "dns_txt"))
    )
    monkeypatch.setattr(
        biz.domain_ownership, "start_domain_verification", AsyncMock(return_value={"token": "t"})
    )
    return business


async def _claim(business_id: uuid.UUID, domain: str, user: _StubUser) -> dict:
    return await biz.check_claim_domain_verification(
        business_id, _payload(domain), db=_StubSession(), current_user=user
    )


# ── the takeover itself ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_anchor_match_claims(env: _StubBusiness) -> None:
    user = _StubUser()
    out = await _claim(env.id, "example-anchor.test", user)
    assert out["verified"] is True
    assert out["claim_status"] == "claimed"
    assert env.owner_id == user.id


@pytest.mark.asyncio
async def test_anchor_mismatch_is_403_and_transfers_nothing(env: _StubBusiness) -> None:
    """The S-22 exploit: prove a throwaway domain, take the profile."""
    original_owner = env.owner_id
    with pytest.raises(HTTPException) as exc:
        await _claim(env.id, "attacker-throwaway.test", _StubUser())
    assert exc.value.status_code == 403
    assert "example-anchor.test" in exc.value.detail  # honest, names the real anchor
    assert env.owner_id == original_owner
    assert env.claim_status == "pre_verified_unclaimed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain",
    [
        "sub.example-anchor.test",          # child of the anchor
        "claim.example-anchor.test",
        "example-anchor.test.evil.test",    # anchor as a prefix label
        "notexample-anchor.test",           # anchor as a string suffix
    ],
)
async def test_subdomain_and_lookalike_rejected(env: _StubBusiness, domain: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await _claim(env.id, domain, _StubUser())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_anchor_is_403(monkeypatch: pytest.MonkeyPatch, env: _StubBusiness) -> None:
    """`pre_verified_source` present but no domain key at all."""
    env.pre_verified_source = {"opt_out_token": "x", "pulled_from": "top500_dataset"}
    with pytest.raises(HTTPException) as exc:
        await _claim(env.id, "anything.test", _StubUser())
    assert exc.value.status_code == 403
    assert "no domain anchor" in exc.value.detail


@pytest.mark.asyncio
async def test_github_only_entity_is_403(env: _StubBusiness) -> None:
    """Imported with a github_org/npm anchor only (`domain: None`, the shape
    bulk_preverify_entities actually writes) — domain proof can't claim it;
    a github-based proof path is its own task."""
    env.pre_verified_source = {
        "domain": None, "github_org": "example-org", "npm_package": None,
        "pulled_from": "top500_dataset", "opt_out_token": "x",
    }
    with pytest.raises(HTTPException) as exc:
        await _claim(env.id, "example-anchor.test", _StubUser())
    assert exc.value.status_code == 403
    assert "GitHub" in exc.value.detail


@pytest.mark.asyncio
async def test_no_pre_verified_source_at_all_is_403(env: _StubBusiness) -> None:
    env.pre_verified_source = None
    with pytest.raises(HTTPException) as exc:
        await _claim(env.id, "example-anchor.test", _StubUser())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_start_rejects_mismatch_before_minting_a_token(env: _StubBusiness) -> None:
    """/claim/domain/start is gated too — no DNS instructions for a domain that
    could never complete the claim."""
    with pytest.raises(HTTPException) as exc:
        await biz.start_claim_domain_verification(
            env.id, _payload("attacker-throwaway.test"), db=_StubSession(), current_user=_StubUser()
        )
    assert exc.value.status_code == 403
    biz.domain_ownership.start_domain_verification.assert_not_called()


@pytest.mark.asyncio
async def test_start_allows_the_anchor(env: _StubBusiness) -> None:
    out = await biz.start_claim_domain_verification(
        env.id, _payload("https://WWW.Example-Anchor.test/"), db=_StubSession(),
        current_user=_StubUser(),
    )
    assert out == {"token": "t"}


# ── normalization: the honest owner must not be tripped by cosmetics ──────────
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain",
    [
        "Example-Anchor.test",                  # case
        "WWW.example-anchor.test",              # www.
        "example-anchor.test.",                 # trailing root dot
        "example-anchor.test:443",              # port
        "https://example-anchor.test",          # scheme
        "https://www.Example-Anchor.test:443/about?x=1",  # all of it + path/query
        "  example-anchor.test  ",              # whitespace
    ],
)
async def test_cosmetic_variants_still_match_the_anchor(env: _StubBusiness, domain: str) -> None:
    user = _StubUser()
    out = await _claim(env.id, domain, user)
    assert out["verified"] is True
    assert env.owner_id == user.id


# ── the shared helper itself ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", "example.com"),
        ("www.example.com", "example.com"),
        ("example.com.", "example.com"),
        ("example.com:8443", "example.com"),
        ("http://example.com/path", "example.com"),
        ("https://user:pw@example.com/", "example.com"),
        ("bücher.de", "xn--bcher-kva.de"),          # IDN -> punycode
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),   # already punycode, stable
        ("WWW.BÜCHER.de.", "xn--bcher-kva.de"),     # all rules at once
        ("wwwexample.com", "wwwexample.com"),       # only the `www.` label goes
    ],
)
def test_normalize_domain(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


def test_normalize_domain_is_idempotent() -> None:
    for raw in ["https://WWW.BÜCHER.de.:443/x", "Example.com", "a.b.c.example.com"]:
        once = normalize_domain(raw)
        assert normalize_domain(once) == once


def test_anchor_domain_normalizes_and_handles_absence() -> None:
    assert anchor_domain({"domain": "WWW.Example.com."}) == "example.com"
    assert anchor_domain({"domain": None, "github_org": "o"}) is None
    assert anchor_domain({}) is None
    assert anchor_domain(None) is None
