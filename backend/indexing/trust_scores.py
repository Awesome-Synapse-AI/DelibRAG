from typing import Optional


def initial_trust_score() -> float:
    return 1.0


def update_trust_score(current: float, delta: float) -> float:
    """Clamp trust score between 0 and 2 for now."""
    return max(0.0, min(2.0, current + delta))
