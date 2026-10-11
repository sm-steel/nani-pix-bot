"""Small vector shapes shared by the stage frames and the reveal overlays. Pure
math, no Pillow: each returns polygon points for ImageDraw."""

import math


def petal_outline(
    base: tuple[float, float], length: float, width: float, angle: float
) -> list[tuple[float, float]]:
    """One sakura petal from `base` along `angle`: widening, rounded, with the
    notch at its tip."""
    half_lobe, notch_depth = 0.38, 0.84
    side = [
        (length * t, width * math.sin(math.pi * t) ** 0.6)
        for t in (0.04 + 0.88 * i / 14 for i in range(15))
    ]
    local = [(u, -v) for u, v in side]
    local += [(length, -width * half_lobe), (length * notch_depth, 0.0)]
    local += [(length, width * half_lobe)]
    local += [(u, v) for u, v in reversed(side)]
    cos, sin = math.cos(angle), math.sin(angle)
    x, y = base
    return [(x + u * cos - v * sin, y + u * sin + v * cos) for u, v in local]
