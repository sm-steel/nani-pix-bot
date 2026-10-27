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


def bone_outline(
    a: tuple[float, float], b: tuple[float, float], thickness: float
) -> list[tuple[float, float]]:
    """A bone from `a` to `b`: a bar `thickness` wide with a pair of round knobs
    (radius 0.75 * thickness) at each end, as one polygon."""
    ax, ay = a
    length = math.hypot(b[0] - ax, b[1] - ay)
    if length == 0:
        return []
    half, knob = thickness / 2, thickness * 0.75
    notch = math.asin(half / knob)  # where the two knobs of an end meet
    steps = 10

    def end(sign: int) -> list[tuple[float, float]]:
        """The knob pair at the far end of the bone (local u, v), `sign` -1 for the near one.
        Walks from the top edge, over both knobs, back to the bottom edge."""
        centre = length if sign > 0 else 0.0
        out = []
        for side in (-1, 1):  # the knob above the bar, then the one below
            lo, hi = (math.pi, math.tau + notch) if side < 0 else (-notch, math.pi)
            for i in range(steps + 1):
                t = lo + (hi - lo) * i / steps
                u, v = knob * math.cos(t), side * half + knob * math.sin(t)
                out.append((centre + sign * u, v))
        return out

    far = end(1)
    near = list(reversed(end(-1)))
    local = far + near
    cos, sin = (b[0] - ax) / length, (b[1] - ay) / length
    return [(ax + u * cos - v * sin, ay + u * sin + v * cos) for u, v in local]
