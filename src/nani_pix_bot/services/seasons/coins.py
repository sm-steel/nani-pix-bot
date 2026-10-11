"""Season Coins (seasons spec §2, §7): what each season game event pays in 🪙,
priced by the run's coin table through the same rules as Season XP."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, XpSource
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import players
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import xp

_REASONS = {
    XpSource.WIN: CurrencyReason.SEASON_WIN,
    XpSource.HOST: CurrencyReason.SEASON_HOST,
    XpSource.FIRST_GUESS: CurrencyReason.SEASON_FIRST_GUESS,
}


def pay(session: Session, event: LoggedEvent, found: xp.Tagged) -> list[xp.Award]:
    table = found.run.coins.as_xp_table()
    paid = xp.awards(
        table, event, bot_game=found.game.hard_mode, first_guess_of_player=found.first_guess
    )
    for award in paid:
        player = session.get(Player, award.player_id)
        if player is None:
            logger.error(
                "no player {recipient_id} to pay {amount} 🪙",
                recipient_id=award.player_id,
                amount=award.amount,
                season_id=found.season.id,
            )
            continue
        entry = wallet.LedgerEntry(
            _REASONS[award.source], game_id=found.game.id, season_id=found.season.id, coins=True
        )
        wallet.credit(session, player, award.amount, entry)
        logger.info(
            "season coins +{amount} 🪙 ({source}) to {recipient}",
            amount=award.amount,
            source=award.source.value,
            recipient=players.describe_player_id(session, award.player_id),
            recipient_id=award.player_id,
            season_id=found.season.id,
            run_id=found.season.run_id,
            game_id=found.game.id,
        )
    return paid
