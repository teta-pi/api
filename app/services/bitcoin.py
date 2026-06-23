"""
Bitcoin OpenTimestamps integration.
Uses the OpenTimestamps calendar servers — NOT OP_RETURN (excluded per BIP-177, 2025-2026).
"""

import hashlib
import logging

logger = logging.getLogger(__name__)


async def submit_hash(file_bytes: bytes) -> bytes | None:
    """
    Compute SHA-256 of file and submit to OpenTimestamps calendar.
    Returns the .ots proof bytes, or None on failure.
    """
    try:
        import opentimestamps.calendar as calendar_mod
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.core.op import OpSHA256

        file_hash = hashlib.sha256(file_bytes).digest()
        ts = Timestamp(file_hash)

        # Submit to public OTS calendars
        calendar_urls = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
        ]

        for url in calendar_urls:
            try:
                cal = calendar_mod.RemoteCalendar(url)
                cal.submit(ts)
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


async def verify_proof(proof_bytes: bytes, file_bytes: bytes) -> dict:
    """
    Verify an existing .ots proof against a file.
    Returns {confirmed: bool, bitcoin_block: int | None}.
    """
    try:
        import io
        from opentimestamps.core.timestamp import Timestamp
        from opentimestamps.core.serialize import StreamDeserializationContext

        buf = io.BytesIO(proof_bytes)
        ctx = StreamDeserializationContext(buf)
        ts = Timestamp.deserialize(ctx, hashlib.sha256(file_bytes).digest())

        # Try to upgrade (check Bitcoin confirmation)
        from opentimestamps.calendar import RemoteCalendar
        calendars = [
            "https://alice.btc.calendar.opentimestamps.org",
            "https://bob.btc.calendar.opentimestamps.org",
        ]
        for url in calendars:
            try:
                cal = RemoteCalendar(url)
                cal.get_timestamp(ts.msg)
                break
            except Exception:
                continue

        # Check if timestamp has Bitcoin attestation
        from opentimestamps.core.timestamp import BitcoinBlockHeaderAttestation
        for attestation in ts.all_attestations():
            if isinstance(attestation, BitcoinBlockHeaderAttestation):
                return {"confirmed": True, "bitcoin_block": attestation.height}

        return {"confirmed": False, "bitcoin_block": None}

    except Exception as e:
        logger.error("OTS verification failed: %s", e)
        return {"confirmed": False, "bitcoin_block": None}
