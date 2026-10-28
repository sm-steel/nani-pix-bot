"""Season award multipliers (Halloween spec): haunted, event-day and boss games
pay more. Integers that multiply together. The boss boost is for guessers
only: a boss game has no human host (`Game.bot_hosted`), so no host award is
paid on it at all, and the HOST exemption below only keeps it that way."""

from nani_pix_bot.models.enums import XpSource
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.seasons import haunting


def coin_multiplier(run: SeasonRun, game: Game, source: XpSource = XpSource.WIN) -> int:
    factor = 1
    if run.haunt is not None and haunting.is_haunted(game):
        factor *= run.haunt.coin_multiplier
    if game.event_day and run.event_day is not None:
        factor *= run.event_day.coin_multiplier
    if game.boss_game and run.group_goal is not None and source is not XpSource.HOST:
        factor *= run.group_goal.boss_coin_multiplier
    return factor


def xp_multiplier(run: SeasonRun, game: Game, source: XpSource = XpSource.WIN) -> int:
    if game.boss_game and run.group_goal is not None and source is not XpSource.HOST:
        return run.group_goal.boss_xp_multiplier
    return 1
