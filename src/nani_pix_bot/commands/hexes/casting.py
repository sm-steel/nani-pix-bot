"""Casting a hex from /hex or a picker tap: the cast commits first, then its notice
goes to the game topic. A notice that never arrives doesn't undo the hex."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.hexes import notices
from nani_pix_bot.commands.hexes.common import now
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.moments import DuelMoment, Person, post_duel_moment
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import hexes
from nani_pix_bot.services.seasons.hexes import HexRefusal, HexRefusedError


@dataclass(frozen=True)
class Cast:
    target_id: int
    kind: HexKind


@dataclass(frozen=True)
class CastReply:
    ok: bool  # the hex was cast (blocked or not)
    text: str  # hex.cast_done, or why it was refused


@dataclass(frozen=True)
class _Done:
    moment: DuelMoment
    hex_id: int
    target: str  # for the log: describe_player_id
    lang: str


def _commit(session: Session, user: User, cast: Cast) -> _Done:
    lang = settings.get_language(session)
    caster = players.get_or_create_player(session, user.id, username=user.username)
    target = session.get(Player, cast.target_id)
    if target is None:
        logger.warning("hex refused: target {target_id} is no player", target_id=cast.target_id)
        raise HexRefusedError(HexRefusal.TARGET_NOT_PLAYED)
    result = hexes.cast(session, caster, target, cast.kind, now())
    opened = hexes.open_hexes(session)
    rule = None if opened is None else hexes.rule_for(opened.run, cast.kind)
    if rule is None:  # cast() just found both, in this same transaction
        raise HexRefusedError(HexRefusal.CLOSED)
    sides = (
        Person(user.id, players.display_name(session, user.id)),
        Person(cast.target_id, players.display_name(session, cast.target_id)),
    )
    card = notices.moment(sides, rule, result.blocked, lang)
    target_log = players.describe_player_id(session, cast.target_id)
    return _Done(card, result.hex.id, target_log, lang)


def _refusal_text(session_factory, user_id: int, error: HexRefusedError, kind: HexKind) -> str:
    """The reply for a refused cast; its transaction already rolled back, so the
    price and the 🪙 balance are read in a fresh scope."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        opened = hexes.open_hexes(session)
        rule = None if opened is None else hexes.rule_for(opened.run, kind)
        balance = 0 if opened is None else wallet.balance(session, user_id, opened.season.id)
    wait = "" if error.wait is None else duration(error.wait, lang)
    price = 0 if rule is None else rule.price
    return i18n.t(
        f"hex.refusal.{error.refusal.value}", lang, wait=wait, price=price, balance=balance
    )


async def cast_hex(context: ContextTypes.DEFAULT_TYPE, user: User, cast: Cast) -> CastReply:
    """Cast, then post the notice. `hexes.cast` logs the cast and any refusal."""
    session_factory = context.bot_data["session_factory"]
    try:
        with session_scope(session_factory) as session:
            done = _commit(session, user, cast)
    except HexRefusedError as error:
        return CastReply(False, _refusal_text(session_factory, user.id, error, cast.kind))
    log = {"hex_id": done.hex_id, "target": done.target, "target_id": cast.target_id}
    if not await post_duel_moment(context, done.moment, **log):
        logger.error(
            "the {moment} notice never reached the game topic", moment=done.moment.name, **log
        )
    return CastReply(True, i18n.t("hex.cast_done", done.lang))
