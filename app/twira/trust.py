"""T — Trust Score. Precomputed, stored in businesses.t_score (SystemSpec v2.1 §3.1)."""

from datetime import datetime, timezone
from math import exp, tanh

from app.models.verification_event import VerificationEvent

LEVEL_W = {1: 0.4, 2: 0.8, 3: 1.0}
SOURCE_W = {
    "official_registry": 1.0,
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
