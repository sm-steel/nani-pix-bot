"""Casting hexes (Halloween spec): who may hex whom, what it costs, the
cooldown per kind, one active hex per target, and the caster's opt-out lock."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from loguru import logger
from sqlalchemy import exists, func, select, union
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import (
    ConsumableKind,
    CurrencyReason,
    EventType,
    HexOutcome,
    XpSource,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import PRANK_HEXES, HexKind, HexRule
from nani_pix_bot.services import events, players
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import schedule, xp
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
    blocked: bool  # an omamori ate the hex
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


def played_ids(season_id: int):
    """Everyone who played in the season: guessed in one of its games, or hosted one."""
    guessed = (
        select(GameGuess.player_id.label("player_id"))
        .join(Game, Game.id == GameGuess.game_id)
        .where(Game.season_id == season_id)
    )
    hosted = select(Game.starter_id.label("player_id")).where(
        Game.season_id == season_id,
        Game.activated_at.is_not(None),
        Game.hard_mode.is_(False),
    )
    return union(guessed, hosted).subquery()


def played_in_season(session: Session, season_id: int, player_id: int) -> bool:
    played = played_ids(season_id)
    return bool(session.scalar(select(exists().where(played.c.player_id == player_id))))


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
    session: Session,
    who: tuple[Player, Player],
    rule: HexRule,
    season_id: int,
    now: datetime,
) -> Hex:
    """The hex row; an omamori of the target's, if any, is spent and the hex lands blocked."""
    caster, target = who
    blocked = consumables.take(session, target.telegram_user_id, ConsumableKind.OMAMORI)
    stamp = naive_utc(now)
    expires = stamp + timedelta(hours=rule.duration_hours) if rule.kind in PRANK_HEXES else None
    row = Hex(
        season_id=season_id,
        kind=rule.kind.value,
        caster_id=caster.telegram_user_id,
        target_id=target.telegram_user_id,
        cast_at=stamp,
        expires_at=expires,
        resolved_at=stamp if blocked else None,
        outcome=HexOutcome.BLOCKED.value if blocked else None,
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
    blocked = row.outcome == HexOutcome.BLOCKED.value
    _log_cast(session, row, target, rule.price)
    _emit_cast(session, row, (caster, target), blocked)
    return CastResult(hex=row, blocked=blocked, price=rule.price)


def _log_cast(session: Session, row: Hex, target: Player, price: int) -> None:
    blocked = row.outcome == HexOutcome.BLOCKED.value
    note = " — blocked by their omamori" if blocked else ""
    logger.info(
        "cast {kind} on {target} for {price} 🪙" + note,
        kind=row.kind,
        target=players.describe_player_id(session, target.telegram_user_id),
        price=price,
        blocked=blocked,
        hex_id=row.id,
        target_id=target.telegram_user_id,
        season_id=row.season_id,
    )


def _emit_cast(session: Session, row: Hex, who: tuple[Player, Player], blocked: bool) -> None:
    caster, target = who
    events.emit(
        session,
        EventType.HEX_CAST,
        events.Involved(actor_id=caster.telegram_user_id, subject_id=target.telegram_user_id),
        season_id=row.season_id,
        kind=row.kind,
        hex_id=row.id,
        outcome=HexOutcome.BLOCKED.value if blocked else None,
        self_prank=False,
    )
    if blocked:
        events.emit(
            session,
            EventType.HEX_BLOCKED,
            events.Involved(actor_id=target.telegram_user_id, subject_id=caster.telegram_user_id),
            season_id=row.season_id,
            kind=row.kind,
            hex_id=row.id,
        )


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


def _active_of(
    session: Session, player_id: int, found: xp.Tagged, kind: HexKind, at: datetime
) -> Hex | None:
    """The player's active hex when it is a `kind` hex of this season."""
    row = active_hex_for(session, player_id, at)
    if row is None or row.kind != kind.value or row.season_id != found.season.id:
        return None
    return row


def _resolve(session: Session, row: Hex, game_id: int, amount: int, at: datetime) -> None:
    """A Kappa/Kitsune went off: close the hex, log it, emit it, queue its notice."""
    row.resolved_at = naive_utc(at)
    row.outcome = HexOutcome.TRIGGERED.value
    row.game_id = game_id
    session.flush()
    logger.info(
        "{kind} hex on {target} triggered for {amount} 🪙 (cast by {caster})",
        kind=row.kind,
        target=players.describe_player_id(session, row.target_id),
        caster=players.describe_player_id(session, row.caster_id),
        amount=amount,
        hex_id=row.id,
        target_id=row.target_id,
        caster_id=row.caster_id,
        season_id=row.season_id,
        game_id=game_id,
    )
    events.emit(
        session,
        EventType.HEX_TRIGGERED,
        events.Involved(actor_id=row.target_id, subject_id=row.caster_id, game_id=game_id),
        season_id=row.season_id,
        kind=row.kind,
        hex_id=row.id,
        amount=amount,
    )
    outbox.enqueue_hex_notice(session, row.id, "triggered")


def _move_coins(session: Session, row: Hex, found: xp.Tagged, amount: int) -> int:
    """Kappa: up to `amount` 🪙 from the hexed player to the caster; returns what moved."""
    target = session.get(Player, row.target_id)
    caster = session.get(Player, row.caster_id)
    moved = min(amount, wallet.balance(session, row.target_id, season_id=row.season_id))
    if target is None or caster is None or moved <= 0:
        return 0
    entry = wallet.LedgerEntry(
        CurrencyReason.HEX_KAPPA, game_id=found.game.id, season_id=row.season_id, coins=True
    )
    wallet.transfer(session, wallet.Party.of(target), wallet.Party.of(caster), moved, entry)
    return moved


def _take_for_house(session: Session, row: Hex, found: xp.Tagged, amount: int) -> int:
    """Kitsune: up to `amount` 🪙 off the hexed player, paid to the house; returns what went."""
    target = session.get(Player, row.target_id)
    taken = min(amount, wallet.balance(session, row.target_id, season_id=row.season_id))
    if target is None or taken <= 0:
        return 0
    entry = wallet.LedgerEntry(
        CurrencyReason.HEX_KITSUNE, game_id=found.game.id, season_id=row.season_id, coins=True
    )
    wallet.debit(session, target, taken, entry)
    return taken


def _kappa(session: Session, event: LoggedEvent, found: xp.Tagged, paid: list[xp.Award]) -> None:
    actor = event.actor_id
    won = sum(a.amount for a in paid if a.player_id == actor and a.source == XpSource.WIN)
    if actor is None or won <= 0:
        return
    row = _active_of(session, actor, found, HexKind.KAPPA, event.occurred_at)
    rule = rule_for(found.run, HexKind.KAPPA)
    if row is None or rule is None:
        return
    moved = _move_coins(session, row, found, won * rule.amount // 100)
    _resolve(session, row, found.game.id, moved, event.occurred_at)


def _kitsune(session: Session, event: LoggedEvent, found: xp.Tagged) -> None:
    actor = event.actor_id
    if actor is None:
        return
    row = _active_of(session, actor, found, HexKind.KITSUNE, event.occurred_at)
    rule = rule_for(found.run, HexKind.KITSUNE)
    if row is None or rule is None:
        return
    moved = _take_for_house(session, row, found, rule.amount)
    _resolve(session, row, found.game.id, moved, event.occurred_at)


def on_season_event(
    session: Session, event: LoggedEvent, found: xp.Tagged, paid: list[xp.Award]
) -> None:
    """Kappa waits for its target's next win that pays coins; Kitsune for the
    next wrong guess. An ofuda doesn't stop a Kitsune: the guess still happened."""
    if event.event_type == EventType.GAME_WON:
        _kappa(session, event, found, paid)
    elif event.event_type == EventType.GUESS and event.data.get("correct") is False:
        _kitsune(session, event, found)


def cancel_open(session: Session, season_id: int, now: datetime) -> int:
    """The season ended: every still-open hex of it is cancelled."""
    stmt = select(Hex).where(Hex.season_id == season_id, Hex.resolved_at.is_(None))
    rows = list(session.scalars(stmt))
    for row in rows:
        row.resolved_at = naive_utc(now)
        row.outcome = HexOutcome.CANCELLED.value
    session.flush()
    logger.info(
        "season ended: cancelled {count} open hex(es)", count=len(rows), season_id=season_id
    )
    return len(rows)


def _self_prank_season(session: Session, run: SeasonRun, player_id: int) -> SeasonSchedule:
    player = session.get(Player, player_id)
    season = schedule.active(session)
    if player is None or season is None or season.run_id != run.run_id:
        raise _refuse(HexRefusal.CLOSED)
    if player.hex_opt_out:
        raise _refuse(HexRefusal.TARGET_OPTED_OUT)
    return season


def apply_self_prank(session: Session, run: SeasonRun, player_id: int, now: datetime) -> Hex:
    """An omikuji kyō: a random prank hex on the drawer themselves. Free, and no
    omamori or cooldown applies. A player already hexed keeps that hex, returned
    unchanged (a new one has caster_id == player_id and cast_at == naive(now))."""
    season = _self_prank_season(session, run, player_id)
    expire_due(session, now)
    existing = active_hex_for(session, player_id, now)
    if existing is not None:
        logger.info(
            "self-prank skipped: already under a {kind} hex",
            kind=existing.kind,
            hex_id=existing.id,
            season_id=season.id,
        )
        return existing
    rules = [rule for rule in run.hexes if rule.kind in PRANK_HEXES]
    if not rules:
        raise _refuse(HexRefusal.UNKNOWN_KIND)
    rule = random.choice(rules)  # noqa: S311 - a game roll, not security
    stamp = naive_utc(now)
    row = Hex(
        season_id=season.id,
        kind=rule.kind.value,
        caster_id=player_id,
        target_id=player_id,
        cast_at=stamp,
        expires_at=stamp + timedelta(hours=rule.duration_hours),
    )
    session.add(row)
    session.flush()
    logger.info(
        "omikuji self-prank: {kind} for {hours} h",
        kind=row.kind,
        hours=rule.duration_hours,
        hex_id=row.id,
        season_id=season.id,
    )
    events.emit(
        session,
        EventType.HEX_CAST,
        events.Involved(actor_id=player_id, subject_id=player_id),
        season_id=season.id,
        kind=row.kind,
        hex_id=row.id,
        outcome=None,
        self_prank=True,
    )
    # no outbox notice: /omikuji posts the Kyō card itself once the draw commits
    return row
