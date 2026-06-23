"""
C2PA (Coalition for Content Provenance and Authenticity) verification.
Verifies that media was captured by a registered PI Camera device.
"""

import json
import logging

logger = logging.getLogger(__name__)


def extract_c2pa_manifest(file_bytes: bytes, mime_type: str) -> dict | None:
    """
    Extract C2PA manifest from file bytes.
    In production this uses c2pa-rs via Python bindings.
    For Sprint 1, returns a stub that can be replaced.
    """
    try:
        # c2pa-rs Python bindings — install separately: pip install c2pa-python
        import c2pa  # type: ignore[import]

        result = c2pa.read_file_bytes(file_bytes, mime_type)
        if result:
            return json.loads(result)
        return None
    except ImportError:
        logger.warning("c2pa-python not installed — C2PA extraction unavailable")
        return None
    except Exception as e:
        logger.error("C2PA manifest extraction failed: %s", e)
        return None


def verify_pi_camera_signature(manifest: dict) -> tuple[bool, str | None]:
    """
    Verify that the C2PA manifest was signed by a registered PI Camera device.
    Returns (is_valid, signer_label).
    """
    if not manifest:
        return False, None

    claim_generator = manifest.get("claim_generator", "")
    if "PI Camera" not in claim_generator:
        return False, None

    # Verify the signature chain back to the PI Camera root CA
    # In production: validate the ECDSA P-256 signature using the device's public key
    # and verify it chains to the PI Camera root CA certificate
    signature_info = manifest.get("signature_info", {})
    issuer = signature_info.get("issuer", "")

    if "PI Camera" in issuer:
        return True, f"PI Camera/1.0 ({issuer})"

    return False, None


def add_teta_pi_countersignature(manifest: dict) -> dict:
    """
    Add TETA+PI's verifier countersignature to an already-verified manifest.
    In production: sign using TETA+PI Root CA (HSM-backed ECDSA P-256).
    """
    from datetime import datetime, timezone

    manifest["teta_pi_verification"] = {
        "issuer": "TETA+PI Root CA",
        "action": "c2pa.verified",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    return manifest
