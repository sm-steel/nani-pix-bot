"""Admin-tunable currency amounts. Defaults are the constants below (the
single source of truth — DRY per CLAUDE.md); `currency_config` rows hold
only what an admin changed via /pixelconfig."""

import enum
from collections.abc import Mapping
from types import MappingProxyType

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_config import CurrencyConfig


class EconomyKey(enum.StrEnum):
    STARTING_BALANCE = "starting_balance"
    WRONG_GUESS = "wrong_guess"
    WRONG_GUESS_CAP = "wrong_guess_cap"
    FIRST_GUESS = "first_guess"
    WIN_STAGE_1 = "win_stage_1"
    WIN_STAGE_2 = "win_stage_2"
    WIN_STAGE_3 = "win_stage_3"
    WIN_STAGE_4 = "win_stage_4"
    WIN_STAGE_5 = "win_stage_5"
    SETTER = "setter"
    PROMPT_TURN = "prompt_turn"


DEFAULT_AMOUNTS: Mapping[EconomyKey, int] = MappingProxyType(
    {
        EconomyKey.STARTING_BALANCE: 50,
        EconomyKey.WRONG_GUESS: 2,
        EconomyKey.WRONG_GUESS_CAP: 10,
        EconomyKey.FIRST_GUESS: 5,
        EconomyKey.WIN_STAGE_1: 40,
        EconomyKey.WIN_STAGE_2: 30,
        EconomyKey.WIN_STAGE_3: 25,
        EconomyKey.WIN_STAGE_4: 20,
        EconomyKey.WIN_STAGE_5: 15,
        EconomyKey.SETTER: 15,
        EconomyKey.PROMPT_TURN: 10,
    }
)

# Index i is the win reward for stage number i + 1.
WIN_STAGE_KEYS: tuple[EconomyKey, ...] = (
    EconomyKey.WIN_STAGE_1,
    EconomyKey.WIN_STAGE_2,
    EconomyKey.WIN_STAGE_3,
    EconomyKey.WIN_STAGE_4,
    EconomyKey.WIN_STAGE_5,
)


def get_amounts(session: Session) -> dict[EconomyKey, int]:
    """Every amount, overrides applied. Rows for keys this version doesn't
    know (e.g. left behind by a rollback) are ignored."""
    overrides = {row.key: row.value for row in session.scalars(select(CurrencyConfig))}
    return {key: overrides.get(key.value, default) for key, default in DEFAULT_AMOUNTS.items()}


def set_amount(session: Session, key: EconomyKey, value: int) -> None:
    if value < 0:
        msg = f"Currency amount for {key} cannot be negative: {value}"
        raise ValueError(msg)
    row = session.get(CurrencyConfig, key.value)
    if row is None:
        session.add(CurrencyConfig(key=key.value, value=value))
    else:
        row.value = value
    logger.info("Currency config {} set to {}", key, value)
