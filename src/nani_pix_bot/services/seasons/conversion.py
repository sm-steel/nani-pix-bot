"""Leftover Season Coins become 💠 when a season ends (seasons spec §2): the
whole 🪙 balance is emptied, and the run's rate decides how much 💠 it buys."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import Currency, CurrencyReason
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonBalance, SeasonSchedule
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import players
from nani_pix_bot.services.economy import wallet


@dataclass(frozen=True)
class Converted:
    players: int
    coins: int
    currency: int


def convert(session: Session, season: SeasonSchedule, run: SeasonRun) -> Converted:
    """Empty every positive 🪙 balance of `season`; `coins // rate` 💠 is paid
    when that is at least 1 (the odd coin is dropped)."""
    rows = session.scalars(
        select(SeasonBalance)
        .where(SeasonBalance.season_id == season.id, SeasonBalance.coins > 0)
        .order_by(SeasonBalance.player_id)
    ).all()
    total = Converted(0, 0, 0)
    for balance in rows:
        player = session.get(Player, balance.player_id)
        if player is None:
            continue
        coins = balance.coins
        currency = coins // run.coins_per_currency
        reason = CurrencyReason.SEASON_CONVERSION
        wallet.debit(
            session, player, coins, wallet.LedgerEntry(reason, season_id=season.id, coins=True)
        )
        if currency:
            wallet.credit(
                session, player, currency, wallet.LedgerEntry(reason, season_id=season.id)
            )
        logger.info(
            "converted {coins} 🪙 into {currency} 💠 for {recipient}",
            coins=coins,
            currency=currency,
            recipient=players.describe_player_id(session, player.telegram_user_id),
            recipient_id=player.telegram_user_id,
            season_id=season.id,
            run_id=run.run_id,
        )
        total = Converted(total.players + 1, total.coins + coins, total.currency + currency)
    return total


def summary(session: Session, season_id: int) -> Converted:
    """What `convert` did, read back from the ledger for the end post."""
    converted = (
        CurrencyTransfer.season_id == season_id,
        CurrencyTransfer.reason == CurrencyReason.SEASON_CONVERSION,
    )
    emptied = select(
        func.count(CurrencyTransfer.id), func.coalesce(func.sum(CurrencyTransfer.amount), 0)
    ).where(*converted, CurrencyTransfer.currency == Currency.SEASON)
    paid = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        *converted, CurrencyTransfer.currency == Currency.BASE
    )
    count, coins = session.execute(emptied).one()
    return Converted(int(count), int(coins), int(session.scalar(paid) or 0))
