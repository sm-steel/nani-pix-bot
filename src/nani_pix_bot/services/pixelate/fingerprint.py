"""A perceptual fingerprint (64-bit difference hash) for telling whether two
images are the same frame — a resized or re-encoded copy still matches,
a different shot does not. The screenshot clue uses it so a buyer is never
sold the round's own image or a frame someone already got (issue #446),
even when the two come from different URLs or one was uploaded."""

import io

from PIL import Image, UnidentifiedImageError

_HASH_SIDE = 8
_BITS = _HASH_SIDE * _HASH_SIDE
_MASK = (1 << _BITS) - 1
# Two fingerprints at most this many bits apart are the same frame. A
# recompressed, resized copy lands within a few bits; different shots sit
# well over a dozen apart (around half the bits for unrelated ones).
SAME_FRAME_MAX_DISTANCE = 8


def fingerprint(data: bytes) -> int:
    """The image's difference hash, as a signed 64-bit int (so it fits a
    BigInteger column). Raises ValueError if `data` isn't an image."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            small = image.convert("L").resize(
                (_HASH_SIDE + 1, _HASH_SIDE), Image.Resampling.LANCZOS
            )
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("cannot fingerprint: not a readable image") from error
    pixels = small.tobytes()  # mode "L": one byte per pixel, row by row
    value = 0
    for row in range(_HASH_SIDE):
        offset = row * (_HASH_SIDE + 1)
        for col in range(_HASH_SIDE):
            value = (value << 1) | (pixels[offset + col] > pixels[offset + col + 1])
    return value - (1 << _BITS) if value >= 1 << (_BITS - 1) else value


def distance(a: int, b: int) -> int:
    """How many of the 64 bits differ (sign-agnostic)."""
    return ((a ^ b) & _MASK).bit_count()


def same_frame(a: int, b: int) -> bool:
    return distance(a, b) <= SAME_FRAME_MAX_DISTANCE
