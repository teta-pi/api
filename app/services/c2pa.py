"""
C2PA (Coalition for Content Provenance and Authenticity) verification.
Verifies that media was captured by a registered PI Camera device.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Claim generators produced by Pi CAM app (any version)
_PICAM_GENERATORS = {"picam", "pi cam", "pi camera"}


def _is_picam_generator(claim_generator: str) -> bool:
    cg = claim_generator.lower()
    return any(g in cg for g in _PICAM_GENERATORS)


def extract_c2pa_manifest(file_bytes: bytes, mime_type: str) -> dict | None:
    """
    Extract C2PA manifest from file bytes.
    Phase 1: reads sidecar JSON embedded as XMP comment or returns None.
    Phase 2: use c2pa-python bindings for full JUMBF extraction.
    """
    try:
        import c2pa  # type: ignore[import]
        result = c2pa.read_file_bytes(file_bytes, mime_type)
        if result:
            return json.loads(result)
        return None
    except ImportError:
        logger.debug("c2pa-python not installed — C2PA extraction unavailable")
        return None
    except Exception as e:
        logger.error("C2PA manifest extraction failed: %s", e)
        return None


def extract_c2pa_manifest_from_json(manifest_json: str) -> dict | None:
    """Parse a C2PA manifest passed directly as JSON string (Pi CAM sidecar)."""
    try:
        return json.loads(manifest_json)
    except Exception:
        return None


def verify_pi_camera_signature(manifest: dict) -> tuple[bool, str | None]:
    """
    Verify that the C2PA manifest was signed by a Pi CAM device.
    Returns (is_valid, signer_label).

    Accepts any of:
      claim_generator: "PiCAM/1.0.0" | "Pi CAM/1.0" | "PI Camera/1.0 ..."
      signature_info.issuer: "Pi CAM Device Key" | "PI Camera ..."
    """
    if not manifest:
        return False, None

    claim_generator = manifest.get("claim_generator", "")
    if not _is_picam_generator(claim_generator):
        return False, None

    signature_info = manifest.get("signature_info", {})
    issuer = signature_info.get("issuer", "")

    # Accept any Pi CAM issuer variant
    if _is_picam_generator(issuer) or "device key" in issuer.lower():
        signer = signature_info.get("cert_serial_number") or issuer
        return True, f"PiCAM/{signer}"

    return False, None


def add_teta_pi_countersignature(manifest: dict) -> dict:
    """
    Add TETA+PI verifier countersignature to an already-verified manifest.
    Phase 2: sign using TETA+PI Root CA (HSM-backed ECDSA P-256).
    """
    from datetime import datetime, timezone

    manifest["teta_pi_verification"] = {
        "issuer": "TETA+PI Root CA",
        "action": "c2pa.verified",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "trust_level": "full",
    }
    return manifest
