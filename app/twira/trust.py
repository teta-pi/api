"""T — Trust Score. Precomputed, stored in businesses.t_score (SystemSpec v2.1 §3.1)."""

from datetime import datetime, timezone
from math import exp, tanh

from app.models.verification_event import VerificationEvent

LEVEL_W = {1: 0.4, 2: 0.8, 3: 1.0}
# Per-method source weight (verification-rework.md §4). Keyed on
# verification_events.source, as actually written by the verify/* routes —
# not on event_type, since domain ownership records the specific mechanism
# ("dns_txt" | "file") rather than a single "domain_verified" source string.
SOURCE_W = {
    # Official Registry Match — external registry (Handelsregister/GLEIF/VAT), highest trust.
    "official_registry": 1.0,
    # Document Upload — dormant weight only; no backend/upload endpoint yet
    # (verification-rework.md §2). Source value TBD when that ships; kept
    # here so the ordering is decided ahead of time: below registry match
    # (self-reported document, not a live third-party check), above domain/email.
    "document_verified": 0.85,
    # Domain Ownership — DNS TXT or well-known-file proof of control over the domain.
    "dns_txt": 0.75,
    "file": 0.75,
    # Business Email Control — weighted below domain/registry: known-issues.md
    # notes the verified mailbox domain isn't cryptographically bound to the
    # entity (any non-free-mailbox address on the domain passes), only a hash
    # of it is recorded.
    "business_email": 0.5,
    # Legacy/placeholder sources — not currently written by any route, kept
    # for backward compatibility with historical or manually-inserted events.
    "c2pa_camera": 0.9,
    "linked_account": 0.6,
    "self_declared": 0.3,
}
LAMBDA = 0.0038  # ~ half-life 180 days; tune later


def trust_score(events: list[VerificationEvent]) -> float:
    """Sum of confirmed verification events with exponential recency decay,
    squashed with tanh so event spam saturates instead of growing unbounded."""
    now = datetime.now(timezone.utc)
    s = 0.0
    for ev in events:
        if ev.ots_status != "confirmed":
            continue
        t_days = max(0, (now - ev.created_at).days)
        level_w = LEVEL_W.get(ev.level, 0.4)
        source_w = SOURCE_W.get(ev.source, 0.3)
        s += level_w * exp(-LAMBDA * t_days) * source_w
    return tanh(s)
