"""
Bitcoin OpenTimestamps integration.
Uses the OpenTimestamps calendar servers — NOT OP_RETURN (excluded per BIP-177, 2025-2026).
"""

import hashlib
import logging

logger = logging.getLogger(__name__)


async def submit_hash(content_hash: bytes) -> bytes | None:
    """
    Submit a precomputed SHA-256 digest to the OpenTimestamps calendar.
    Returns the .ots proof bytes, or None on failure.
    """
    try:
        import opentimestamps.calendar as calendar_mod
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.core.op import OpSHA256

        ts = Timestamp(content_hash)

        # Submit to public OTS calendars
        calendar_urls = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
        ]

        for url in calendar_urls:
            try:
                cal = calendar_mod.RemoteCalendar(url)
                # RemoteCalendar.submit() takes the raw digest and returns a
                # Timestamp attesting to it — merge that into ours, don't pass ts in.
                remote_ts = cal.submit(content_hash)
                ts.merge(remote_ts)
                break
            except Exception as e:
                logger.warning("OTS calendar %s failed: %s", url, e)
                continue

        # Serialize the timestamp to bytes
        import io
        from opentimestamps.core.serialize import StreamSerializationContext

        buf = io.BytesIO()
        ctx = StreamSerializationContext(buf)
        ts.serialize(ctx)
        return buf.getvalue()

    except Exception as e:
        logger.error("OpenTimestamps submission failed: %s", e)
        return None


async def verify_proof(proof_bytes: bytes, content_hash: bytes) -> dict:
    """
    Verify an existing .ots proof against a file's precomputed SHA-256 digest.
    Returns {confirmed: bool, bitcoin_block: int | None, proof_bytes: bytes | None}.

    proof_bytes in the result is the re-serialized timestamp if the calendar
    gave us anything new (a fresh pending attestation, or the Bitcoin
    attestation itself) — callers should write it back to storage so the next
    run doesn't have to re-fetch the same calendar state. It's None when
    nothing changed (or on error), so callers can skip the write.
    """
    try:
        import io
        from opentimestamps.core.timestamp import Timestamp, BitcoinBlockHeaderAttestation
        from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext

        buf = io.BytesIO(proof_bytes)
        ctx = StreamDeserializationContext(buf)
        ts = Timestamp.deserialize(ctx, content_hash)

        attestations_before = {a for _, a in ts.all_attestations()}

        # Try to upgrade (check Bitcoin confirmation)
        from opentimestamps.calendar import RemoteCalendar
        calendars = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
        ]
        for url in calendars:
            try:
                cal = RemoteCalendar(url)
                # Same pattern as submit_hash: get_timestamp() returns a
                # Timestamp for our commitment with whatever the calendar
                # knows now — merge it in, don't discard it.
                remote_ts = cal.get_timestamp(ts.msg)
                ts.merge(remote_ts)
                break
            except Exception as e:
                logger.warning("OTS calendar %s failed: %s", url, e)
                continue

        attestations_after = {a for _, a in ts.all_attestations()}
        new_proof_bytes = None
        if attestations_after != attestations_before:
            out = io.BytesIO()
            ts.serialize(StreamSerializationContext(out))
            new_proof_bytes = out.getvalue()

        # Check if timestamp has Bitcoin attestation
        for attestation in attestations_after:
            if isinstance(attestation, BitcoinBlockHeaderAttestation):
                return {
                    "confirmed": True,
                    "bitcoin_block": attestation.height,
                    "proof_bytes": new_proof_bytes,
                }

        return {"confirmed": False, "bitcoin_block": None, "proof_bytes": new_proof_bytes}

    except Exception as e:
        logger.error("OTS verification failed: %s", e)
        return {"confirmed": False, "bitcoin_block": None, "proof_bytes": None}
