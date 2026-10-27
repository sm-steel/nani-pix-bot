"""The one smoothstep, shared by the reveal effects, overlays and season styles."""


def smoothstep(t: float) -> float:
    """0 at or below 0, 1 at or above 1, an S-curve between."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)
