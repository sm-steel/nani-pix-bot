"""Rarity -> 💠 reward (admin-tunable via /pixelconfig) and points (fixed)."""

from collections.abc import Mapping
from types import MappingProxyType

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import EconomyKey

POINTS: Mapping[Rarity, int] = MappingProxyType(
    {Rarity.BRONZE: 1, Rarity.SILVER: 3, Rarity.GOLD: 8, Rarity.PLATINUM: 20}
)
_KEYS: Mapping[Rarity, EconomyKey] = MappingProxyType(
    {
        Rarity.BRONZE: EconomyKey.ACHIEVEMENT_BRONZE,
        Rarity.SILVER: EconomyKey.ACHIEVEMENT_SILVER,
        Rarity.GOLD: EconomyKey.ACHIEVEMENT_GOLD,
        Rarity.PLATINUM: EconomyKey.ACHIEVEMENT_PLATINUM,
    }
)


def reward(session: Session, rarity: Rarity) -> int:
    return config.get_amounts(session)[_KEYS[rarity]]
