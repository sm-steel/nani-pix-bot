"""Casting hexes (Halloween spec): who may hex whom, what it costs, the
cooldown per kind, one active hex per target, and the caster's opt-out lock."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from loguru import logger
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, EventType, HexOutcome
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import PRANK_HEXES, HexKind, HexRule
from nani_pix_bot.services import events, players
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.seasons.hex_marks import active_hex_for as active_hex_for
from nani_pix_bot.services.seasons.hex_marks import naive_utc

OPT_OUT_LOCK = timedelta(hours=24)


class HexRefusal(StrEnum):
    CLOSED = "closed"
    UNKNOWN_KIND = "unknown_kind"
    SELF = "self"
    CASTER_NOT_PLAYED = "caster_not_played"
    TARGET_NOT_PLAYED = "target_not_played"
    CASTER_OPTED_OUT = "caster_opted_out"
    TARGET_OPTED_OUT = "target_opted_out"
    ALREADY_HEXED = "already_hexed"
    COOLDOWN = "cooldown"
    INSUFFICIENT = "insufficient"
    OPT_OUT_LOCKED = "opt_out_locked"


class HexRefusedError(Exception):
    def __init__(self, refusal: HexRefusal, wait: timedelta | None = None) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal
        self.wait = wait


@dataclass(frozen=True)
class OpenHexes:
    season: SeasonSchedule
    run: SeasonRun


@dataclass(frozen=True)
class CastResult:
    hex: Hex
    blocked: bool  # always False until the ofuda block lands
    price: int


def _refuse(refusal: HexRefusal, wait: timedelta | None = None) -> HexRefusedError:
    logger.warning(
        "hex refused: {refusal}",
        refusal=refusal.value,
        wait=None if wait is None else wait.total_seconds(),
    )
    return HexRefusedError(refusal, wait)


def open_hexes(session: Session) -> OpenHexes | None:
    season = schedule.active(session)
    if season is None:
        return None
    run = registry.get(season.run_id)
    if run is None or not run.hexes:
        return None
    return OpenHexes(season, run)


def rule_for(run: SeasonRun, kind: HexKind) -> HexRule | None:
    return next((rule for rule in run.hexes if rule.kind == kind), None)


def played_in_season(session: Session, season_id: int, player_id: int) -> bool:
    guessed = exists().where(
        GameGuess.player_id == player_id,
        GameGuess.game_id == Game.id,
        Game.season_id == season_id,
    )
    hosted = exists().where(
        Game.season_id == season_id,
        Game.starter_id == player_id,
        Game.activated_at.is_not(None),
        Game.hard_mode.is_(False),
    )
    return bool(session.scalar(select(guessed)) or session.scalar(select(hosted)))


def cooldown_left(session: Session, caster_id: int, rule: HexRule, now: datetime) -> timedelta:
    cooldown = timedelta(minutes=rule.cooldown_minutes)
    stamp = naive_utc(now)
    stmt = (
        select(Hex.cast_at)
        .where(
            Hex.caster_id == caster_id,
            Hex.kind == rule.kind.value,
            Hex.caster_id != Hex.target_id,
            Hex.cast_at > stamp - cooldown,
        )
        .order_by(Hex.cast_at.desc())
        .limit(1)
    )
    last = session.scalars(stmt).first()
    return timedelta(0) if last is None else last + cooldown - stamp


def expire_due(session: Session, now: datetime) -> int:
    stmt = select(Hex).where(
        Hex.resolved_at.is_(None),
        Hex.expires_at.is_not(None),
        Hex.expires_at <= naive_utc(now),
    )
    rows = list(session.scalars(stmt))
    for row in rows:
        row.resolved_at = row.expires_at
        row.outcome = HexOutcome.EXPIRED.value
        logger.info(
            "hex {kind} on {target} expired",
            kind=row.kind,
            target=players.describe_player_id(session, row.target_id),
            hex_id=row.id,
            target_id=row.target_id,
            season_id=row.season_id,
        )
    session.flush()
    return len(rows)


def _check_players(session: Session, season_id: int, caster: Player, target: Player) -> None:
    caster_id, target_id = caster.telegram_user_id, target.telegram_user_id
    if caster_id == target_id:
        raise _refuse(HexRefusal.SELF)
    if not played_in_season(session, season_id, caster_id):
        raise _refuse(HexRefusal.CASTER_NOT_PLAYED)
    if not played_in_season(session, season_id, target_id):
        raise _refuse(HexRefusal.TARGET_NOT_PLAYED)
    if caster.hex_opt_out:
        raise _refuse(HexRefusal.CASTER_OPTED_OUT)
    if target.hex_opt_out:
        raise _refuse(HexRefusal.TARGET_OPTED_OUT)


def _check_cast(
    session: Session, season_id: int, who: tuple[Player, Player], rule: HexRule, now: datetime
) -> None:
    caster, target = who
    _check_players(session, season_id, caster, target)
    wait = cooldown_left(session, caster.telegram_user_id, rule, now)
    if wait > timedelta(0):
        raise _refuse(HexRefusal.COOLDOWN, wait)
    if active_hex_for(session, target.telegram_user_id, now) is not None:
        raise _refuse(HexRefusal.ALREADY_HEXED)


def _pay(session: Session, caster: Player, season_id: int, price: int) -> None:
    entry = wallet.LedgerEntry(CurrencyReason.HEX_CAST, season_id=season_id, coins=True)
    try:
        wallet.debit(session, caster, price, entry)
    except wallet.InsufficientCurrencyError:
        raise _refuse(HexRefusal.INSUFFICIENT) from None


def _write_hex(
    session: Session, who: tuple[Player, Player], rule: HexRule, season_id: int, now: datetime
) -> Hex:
    caster, target = who
    stamp = naive_utc(now)
    expires = stamp + timedelta(hours=rule.duration_hours) if rule.kind in PRANK_HEXES else None
    row = Hex(
        season_id=season_id,
        kind=rule.kind.value,
        caster_id=caster.telegram_user_id,
        target_id=target.telegram_user_id,
        cast_at=stamp,
        expires_at=expires,
    )
    session.add(row)
    session.flush()
    return row


def cast(
    session: Session, caster: Player, target: Player, kind: HexKind, now: datetime
) -> CastResult:
    expire_due(session, now)
    opened = open_hexes(session)
    if opened is None:
        raise _refuse(HexRefusal.CLOSED)
    rule = rule_for(opened.run, kind)
    if rule is None:
        raise _refuse(HexRefusal.UNKNOWN_KIND)
    season_id = opened.season.id
    _check_cast(session, season_id, (caster, target), rule, now)
    _pay(session, caster, season_id, rule.price)
    row = _write_hex(session, (caster, target), rule, season_id, now)
    logger.info(
        "cast {kind} on {target} for {price} 🪙",
        kind=kind.value,
        target=players.describe_player_id(session, target.telegram_user_id),
        price=rule.price,
        hex_id=row.id,
        target_id=target.telegram_user_id,
        season_id=season_id,
    )
    events.emit(
        session,
        EventType.HEX_CAST,
        events.Involved(actor_id=caster.telegram_user_id, subject_id=target.telegram_user_id),
        season_id=season_id,
        kind=kind.value,
        hex_id=row.id,
        outcome=None,
        self_prank=False,
    )
    return CastResult(hex=row, blocked=False, price=rule.price)


def _last_cast_at(session: Session, player_id: int) -> datetime | None:
    stmt = select(func.max(Hex.cast_at)).where(
        Hex.caster_id == player_id, Hex.target_id != Hex.caster_id
    )
    return session.scalar(stmt)


def set_opt_out(session: Session, player: Player, off: bool, now: datetime) -> None:
    """`off` opts the player out of hexes. Opting out is locked for OPT_OUT_LOCK
    after the player last hexed someone else; opting back in never is."""
    if off:
        last = _last_cast_at(session, player.telegram_user_id)
        if last is not None:
            wait = last + OPT_OUT_LOCK - naive_utc(now)
            if wait > timedelta(0):
                raise _refuse(HexRefusal.OPT_OUT_LOCKED, wait)
    player.hex_opt_out = off
    session.flush()
    logger.info("hex opt-out {state}", state="on" if off else "off", opted_out=off)
