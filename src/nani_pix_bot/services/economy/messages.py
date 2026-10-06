"""Renders a services/economy Earnings value into the extra lines a
/guess reply or a game caption gets — "" when nothing was earned, so
callers can always just append it."""

from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy.earning import Earnings


def _setter_name(session: Session, game: Game, lang: str) -> str:
    setter = session.get(Player, game.starter_id)
    if setter is not None and setter.username:
        return f"@{setter.username}"
    return i18n.t("economy.setter_fallback_name", lang)


def earnings_suffix(
    session: Session, game: Game, earnings: Earnings, lang: str, player_name: str
) -> str:
    lines: list[str] = []
    if earnings.player_total:
        lines.append(i18n.t("economy.earned", lang, name=player_name, amount=earnings.player_total))
    if earnings.setter:
        lines.append(
            i18n.t(
                "economy.setter_bonus",
                lang,
                name=_setter_name(session, game, lang),
                amount=earnings.setter,
            )
        )
    if earnings.bounty:
        lines.append(i18n.t("economy.bounty_won", lang, name=player_name, amount=earnings.bounty))
    if earnings.compensation:
        lines.append(
            i18n.t("economy.compensation", lang, name=player_name, amount=earnings.compensation)
        )
    return "".join(f"\n{line}" for line in lines)
