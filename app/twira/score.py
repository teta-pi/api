"""TWIRA assembly — α·T + β·I + γ·P with env-overridable static weights (v0)."""

import os

ALPHA = float(os.environ.get("TWIRA_ALPHA", 0.4))
BETA = float(os.environ.get("TWIRA_BETA", 0.4))
GAMMA = float(os.environ.get("TWIRA_GAMMA", 0.2))

# v1 (post-launch): log (query, clicked_entity) pairs -> fit weights by
# logistic regression. This is the data moat closing.


def twira_score(t: float, i: float, p: float) -> float:
    return ALPHA * t + BETA * i + GAMMA * p
