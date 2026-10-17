"""Splitting the images posted after a reveal into Telegram albums."""

from collections.abc import Sequence

ALBUM_LIMIT = 10  # Telegram's cap on photos per media group


def batches(images: Sequence[bytes]) -> list[list[bytes]]:
    """Consecutive chunks of at most ALBUM_LIMIT images; [] for none."""
    return [list(images[i : i + ALBUM_LIMIT]) for i in range(0, len(images), ALBUM_LIMIT)]
