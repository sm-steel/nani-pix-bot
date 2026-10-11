"""Stacking of percentage clue discounts."""


def apply(base: int, *percents: int) -> int:
    """`base` after each positive percent off in turn, rounded down once at the
    end and never below 1 💠. No positive percent leaves `base` untouched."""
    kept, whole = base, 1
    for percent in (p for p in percents if p > 0):
        kept *= 100 - percent
        whole *= 100
    return max(1, kept // whole) if whole > 1 else base
