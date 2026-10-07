"""Device content-signature verification (1.29).

Contract with `teta-pi/pi-cam` 14.12, documented in `docs/api.md` (infra):
the device signs the **hex-encoded SHA-256 hash of the file's raw bytes**
(not the file itself, not the manifest) with its ECDSA P-256 private key and
sends the DER signature base64-encoded as `content_signature` alongside
`signature_alg` ("ecdsa-with-SHA256"). This module recomputes that hash
server-side and verifies the signature against the device's stored public
key — it never trusts anything client-asserted.

Deliberately separate from `c2pa.py`: this proves "this device's key signed
this exact file", nothing about C2PA manifest claims (still gated off,
known-issues §6.8 / security.md S-27). Don't conflate the two signals.
"""

from __future__ import annotations

import base64
import hashlib
import logging

logger = logging.getLogger(__name__)

_SUPPORTED_ALG = "ecdsa-with-SHA256"


def is_valid_device_public_key(pem: str) -> bool:
    """True iff `pem` is a real SPKI/PEM-encoded ECDSA P-256 public key.

    Used at `POST /devices/register` so the column can't keep accepting
    arbitrary text (prod has `"testpubkey"` and bare hex strings from before
    this check existed — those devices simply fail signature verification
    going forward, by design, not re-validated retroactively).
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        key = load_pem_public_key(pem.encode())
        return isinstance(key, ec.EllipticCurvePublicKey) and isinstance(
            key.curve, ec.SECP256R1
        )
    except Exception:
        return False


def verify_content_signature(
    file_bytes: bytes,
    content_signature: str | None,
    signature_alg: str | None,
    device_public_key_pem: str,
) -> bool:
    """Verify `content_signature` over sha256(file_bytes).hexdigest() using
    the device's stored public key. Returns False (never raises) on any
    missing field, wrong algorithm, malformed input, or genuine signature
    mismatch — a failure here must never 500 the upload, only leave the
    trust signal honestly False."""
    if not content_signature or not signature_alg:
        return False
    if signature_alg != _SUPPORTED_ALG:
        logger.info("Unsupported signature_alg %r — treating as unverified", signature_alg)
        return False

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key = load_pem_public_key(device_public_key_pem.encode())
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False

        content_hash_hex = hashlib.sha256(file_bytes).hexdigest()
        der_sig = base64.b64decode(content_signature)

        public_key.verify(der_sig, content_hash_hex.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception as e:
        logger.info("content_signature verification failed: %s", e)
        return False
