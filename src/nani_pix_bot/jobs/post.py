"""One announcement as it will be sent: text, optional card, where it goes."""

from dataclasses import dataclass

from nani_pix_bot.services.cards import PodiumCard, SeasonBanner, UnlockCard


@dataclass(frozen=True)
class Post:
    row_ids: tuple[int, ...]
    text: str
    card: UnlockCard | PodiumCard | SeasonBanner | None = None
    avatar_ids: tuple[int, ...] = ()
    batch_id: int | None = None
    background_slot: str | None = None
    background: bytes | None = None
    dm_to: int | None = None  # a player's DM instead of the topic
