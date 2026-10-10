"""1.30 — bitcoin_confirmed stuck at 0 forever (known-issues §6.8 D3).

Two stacked bugs, both "the upgraded result gets thrown away":

1. `verify_proof()` called `RemoteCalendar.get_timestamp()` and discarded the
   return value instead of merging it in, so an upgraded (possibly
   Bitcoin-attested) Timestamp never replaced the locally-held pending one.
2. Once that was fixed, live verification against prod's 7 real pending
   proofs still produced 0 confirmations — all calendar lookups 404'd. Root
   cause: `alice.btc.calendar.opentimestamps.org` wraps every submission in
   its own nonce op (`OpAppend` + `OpSHA256`) before attesting, so the real
   commitment it tracks is a *nested* leaf, not `ts.msg` (the raw
   content_hash). Querying `ts.msg` asks about a commitment the calendar was
   never given. Confirmed directly against prod: `curl .../timestamp/<ts.msg
   hex>` → 404, `curl .../timestamp/<the nested leaf's msg hex>` → 200.

Fixed by walking the ops tree (same way the real `ots upgrade` CLI's
`directly_verified()` does) to find the sub-stamp that actually holds the
`PendingAttestation`, querying its own calendar URI with its own `.msg`, and
merging the result into that sub-stamp in place.

These tests build real `opentimestamps` Timestamp objects (the library is a
hard dependency, not mocked away) and only fake the network-facing
`RemoteCalendar` class, so the tree-walk/merge/serialize logic under test is
real.
"""

import io

import pytest

from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import Timestamp

from app.services import bitcoin as bitcoin_service

CONTENT_HASH = bytes(range(32))  # deterministic 32-byte "sha256 digest"

ALICE = "https://alice.btc.calendar.opentimestamps.org"
BOB = "https://bob.btc.calendar.opentimestamps.org"


def _serialize(ts: Timestamp) -> bytes:
    buf = io.BytesIO()
    ts.serialize(StreamSerializationContext(buf))
    return buf.getvalue()


def _flat_pending_proof(uri: str = ALICE) -> bytes:
    """Attestation directly on the top-level msg (no calendar nonce op) —
    the shape `submit_hash`'s local `Timestamp(content_hash)` starts as."""
    ts = Timestamp(CONTENT_HASH)
    ts.attestations.add(PendingAttestation(uri))
    return _serialize(ts)


def _nonced_pending_proof(uri: str = ALICE) -> bytes:
    """The real shape calendars actually return: content_hash -> OpAppend
    (calendar's nonce) -> OpSHA256 -> leaf with the PendingAttestation. This
    is what's actually stored in prod's media.bitcoin_proof today."""
    ts = Timestamp(CONTENT_HASH)
    nonced = ts.ops.add(OpAppend(bytes(range(8))))
    leaf = nonced.ops.add(OpSHA256())
    leaf.attestations.add(PendingAttestation(uri))
    return _serialize(ts), leaf.msg


class _FakeCalendar:
    """Stand-in for opentimestamps.calendar.RemoteCalendar."""

    # url -> callable(commitment) -> Timestamp, or an Exception to raise
    behavior: dict = {}

    def __init__(self, url, user_agent="python-opentimestamps"):
        self.url = url

    def get_timestamp(self, commitment, timeout=None):
        action = self.behavior[self.url]
        if isinstance(action, Exception) or (isinstance(action, type) and issubclass(action, Exception)):
            raise action
        return action(commitment)


@pytest.fixture(autouse=True)
def _patch_remote_calendar(monkeypatch):
    monkeypatch.setattr("opentimestamps.calendar.RemoteCalendar", _FakeCalendar)
    _FakeCalendar.behavior = {}
    yield
    _FakeCalendar.behavior = {}


@pytest.mark.asyncio
async def test_bitcoin_attestation_on_nested_calendar_leaf_confirms_and_persists():
    """The real-world shape: the PendingAttestation lives on a nonced leaf,
    not on ts.msg. The lookup must use the leaf's own msg (what the calendar
    actually tracks), and the result must independently re-verify."""
    proof, leaf_msg = _nonced_pending_proof()

    def respond(commitment):
        assert commitment == leaf_msg  # must query the nested leaf, not ts.msg
        ts = Timestamp(commitment)
        ts.attestations.add(BitcoinBlockHeaderAttestation(123456))
        return ts

    _FakeCalendar.behavior = {ALICE: respond}

    result = await bitcoin_service.verify_proof(proof, CONTENT_HASH)

    assert result["confirmed"] is True
    assert result["bitcoin_block"] == 123456
    assert result["proof_bytes"] is not None

    buf = io.BytesIO(result["proof_bytes"])
    ts = Timestamp.deserialize(StreamDeserializationContext(buf), CONTENT_HASH)
    attestations = [a for _, a in ts.all_attestations()]
    assert any(isinstance(a, BitcoinBlockHeaderAttestation) for a in attestations)


@pytest.mark.asyncio
async def test_still_pending_stays_unconfirmed_without_rewrite():
    """Calendar echoes back the same pending attestation we already have —
    nothing new, so confirmed stays False and proof_bytes is None (no
    pointless DB write)."""

    def respond(commitment):
        ts = Timestamp(commitment)
        ts.attestations.add(PendingAttestation(ALICE))
        return ts

    _FakeCalendar.behavior = {ALICE: respond}

    result = await bitcoin_service.verify_proof(_flat_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None


@pytest.mark.asyncio
async def test_calendar_unreachable_returns_false_without_raising():
    _FakeCalendar.behavior = {ALICE: ConnectionError("timed out")}

    result = await bitcoin_service.verify_proof(_flat_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None


@pytest.mark.asyncio
async def test_each_pending_attestation_is_queried_against_its_own_calendar():
    """A proof can carry more than one calendar's PendingAttestation
    (merged from multiple submissions). Each must be looked up against its
    own URI — one calendar being down must not block the other confirming."""
    ts = Timestamp(CONTENT_HASH)
    ts.attestations.add(PendingAttestation(ALICE))
    ts.attestations.add(PendingAttestation(BOB))
    proof = _serialize(ts)

    def bob_confirms(commitment):
        out = Timestamp(commitment)
        out.attestations.add(BitcoinBlockHeaderAttestation(777))
        return out

    _FakeCalendar.behavior = {
        ALICE: ConnectionError("down"),
        BOB: bob_confirms,
    }

    result = await bitcoin_service.verify_proof(proof, CONTENT_HASH)

    assert result["confirmed"] is True
    assert result["bitcoin_block"] == 777


@pytest.mark.asyncio
async def test_corrupt_proof_bytes_returns_false_without_raising():
    """Malformed/garbage proof bytes must not crash the periodic task."""
    result = await bitcoin_service.verify_proof(b"not a real ots proof", CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None
