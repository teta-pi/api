"""1.30 — bitcoin_confirmed stuck at 0 forever (known-issues §6.8 D3).

`verify_proof()` called `RemoteCalendar.get_timestamp()` and threw the result
away, so the upgraded (possibly Bitcoin-attested) Timestamp never replaced the
locally-held one and `all_attestations()` kept seeing only the original
pending calendar attestation. Fixed by merging the remote result in, the same
pattern already used in `submit_hash()`.

These tests build real `opentimestamps` Timestamp objects (the library is a
hard dependency, not mocked away) and only fake the network-facing
`RemoteCalendar` class, so the merge/serialize logic under test is real.
"""

import io

import pytest

from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation
from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import Timestamp

from app.services import bitcoin as bitcoin_service

CONTENT_HASH = bytes(range(32))  # deterministic 32-byte "sha256 digest"


def _serialize(ts: Timestamp) -> bytes:
    buf = io.BytesIO()
    ts.serialize(StreamSerializationContext(buf))
    return buf.getvalue()


def _pending_proof(uri: str = "https://alice.btc.calendar.opentimestamps.org") -> bytes:
    ts = Timestamp(CONTENT_HASH)
    ts.attestations.add(PendingAttestation(uri))
    return _serialize(ts)


class _FakeCalendar:
    """Stand-in for opentimestamps.calendar.RemoteCalendar."""

    # url -> callable(commitment) -> Timestamp, or an Exception instance/class to raise
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
async def test_bitcoin_attestation_confirms_and_updates_proof():
    """Calendar now has a BitcoinBlockHeaderAttestation → confirmed True and
    the returned proof_bytes actually contains it (not just the metadata)."""

    def respond(commitment):
        ts = Timestamp(commitment)
        ts.attestations.add(BitcoinBlockHeaderAttestation(123456))
        return ts

    _FakeCalendar.behavior = {
        "https://alice.btc.calendar.opentimestamps.org": respond,
        "https://bob.btc.calendar.opentimestamps.org": respond,
    }

    result = await bitcoin_service.verify_proof(_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is True
    assert result["bitcoin_block"] == 123456
    assert result["proof_bytes"] is not None

    # The persisted proof must independently re-verify as confirmed, with no
    # further calendar calls needed.
    buf = io.BytesIO(result["proof_bytes"])
    ts = Timestamp.deserialize(StreamDeserializationContext(buf), CONTENT_HASH)
    attestations = [a for _, a in ts.all_attestations()]
    assert any(isinstance(a, BitcoinBlockHeaderAttestation) for a in attestations)


@pytest.mark.asyncio
async def test_still_pending_stays_unconfirmed_without_rewrite():
    """Calendar echoes back the same pending attestation we already have —
    nothing new, so confirmed stays False and no pointless DB write is
    signalled (proof_bytes is None)."""

    def respond(commitment):
        ts = Timestamp(commitment)
        ts.attestations.add(PendingAttestation("https://alice.btc.calendar.opentimestamps.org"))
        return ts

    _FakeCalendar.behavior = {
        "https://alice.btc.calendar.opentimestamps.org": respond,
        "https://bob.btc.calendar.opentimestamps.org": respond,
    }

    result = await bitcoin_service.verify_proof(_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None


@pytest.mark.asyncio
async def test_calendar_unreachable_returns_false_without_raising():
    _FakeCalendar.behavior = {
        "https://alice.btc.calendar.opentimestamps.org": ConnectionError("timed out"),
        "https://bob.btc.calendar.opentimestamps.org": ConnectionError("timed out"),
    }

    result = await bitcoin_service.verify_proof(_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None


@pytest.mark.asyncio
async def test_falls_back_to_second_calendar_when_first_is_down():
    """First calendar in the list is unreachable; the second still has a
    Bitcoin attestation and should be used."""

    def respond(commitment):
        ts = Timestamp(commitment)
        ts.attestations.add(BitcoinBlockHeaderAttestation(777))
        return ts

    _FakeCalendar.behavior = {
        "https://alice.btc.calendar.opentimestamps.org": ConnectionError("down"),
        "https://bob.btc.calendar.opentimestamps.org": respond,
    }

    result = await bitcoin_service.verify_proof(_pending_proof(), CONTENT_HASH)

    assert result["confirmed"] is True
    assert result["bitcoin_block"] == 777


@pytest.mark.asyncio
async def test_corrupt_proof_bytes_returns_false_without_raising():
    """Malformed/garbage proof bytes (e.g. a bad historical write) must not
    crash the periodic task — verify_proof should degrade to unconfirmed."""

    _FakeCalendar.behavior = {
        "https://alice.btc.calendar.opentimestamps.org": lambda c: Timestamp(c),
        "https://bob.btc.calendar.opentimestamps.org": lambda c: Timestamp(c),
    }

    result = await bitcoin_service.verify_proof(b"not a real ots proof", CONTENT_HASH)

    assert result["confirmed"] is False
    assert result["bitcoin_block"] is None
    assert result["proof_bytes"] is None
