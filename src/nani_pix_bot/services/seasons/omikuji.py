"""The daily omikuji draw (Halloween spec): one slip per player per local day in
a season whose run has omikuji outcomes. A slip pays season coins, may add a
charm, and a kyō puts a random prank hex on the drawer themselves."""

import enum
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.models.halloween import Hex, OmikujiDraw
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import OmikujiKind, OmikujiOutcome
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.cosmetics.looks import SEASON_ART_DIR
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import hexes, schedule
from nani_pix_bot.services.seasons.hex_marks import naive_utc
from nani_pix_bot.services.settings import get_group_timezone

_RNG = random.SystemRandom()


class OmikujiRefusal(enum.StrEnum):
    CLOSED = "closed"  # no ACTIVE season, or its run has omikuji == ()
    ALREADY = "already"  # drew this local day


class OmikujiRefusedError(Exception):
    def __init__(self, refusal: OmikujiRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class Draw:
    run: SeasonRun
    outcome: OmikujiOutcome
    local_date: date
    prank: Hex | None  # None: no prank (not KYO, or spared)
    spared: bool  # KYO whose prank was skipped (opted out / already hexed)


def local_day(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def pick(outcomes: Sequence[OmikujiOutcome], rng: random.Random) -> OmikujiOutcome:
    return rng.choices(outcomes, weights=[o.weight for o in outcomes])[0]


def drawn_today(session: Session, season_id: int, player_id: int, day: date) -> OmikujiDraw | None:
    stmt = select(OmikujiDraw).where(
        OmikujiDraw.season_id == season_id,
        OmikujiDraw.player_id == player_id,
        OmikujiDraw.local_date == day,
    )
    return session.scalars(stmt).first()


def _refuse(refusal: OmikujiRefusal) -> OmikujiRefusedError:
    logger.warning("omikuji refused: {refusal}", refusal=refusal.value)
    return OmikujiRefusedError(refusal)


def _resolve(session: Session) -> tuple[SeasonSchedule, SeasonRun]:
    season = schedule.active(session)
    run = None if season is None else registry.get(season.run_id)
    if season is None or run is None or not run.omikuji:
        raise _refuse(OmikujiRefusal.CLOSED)
    return season, run


def _insert_row(session: Session, row: OmikujiDraw) -> None:
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:
        raise _refuse(OmikujiRefusal.ALREADY) from None


def _pay(
    session: Session, player: Player, season: SeasonSchedule, run: SeasonRun, o: OmikujiOutcome
) -> None:
    if o.coins > 0:
        entry = wallet.LedgerEntry(CurrencyReason.SEASON_OMIKUJI, season_id=season.id, coins=True)
        wallet.credit(session, player, o.coins, entry)
    if o.consumable is not None:
        consumables.add(session, player.telegram_user_id, o.consumable, f"{run.run_id} omikuji")


def _prank(session: Session, player: Player, run: SeasonRun, now: datetime) -> Hex | None:
    """The kyō prank, or None when the player is spared it (opted out or already hexed)."""
    if player.hex_opt_out or hexes.active_hex_for(session, player.telegram_user_id, now):
        return None
    return hexes.apply_self_prank(session, run, player.telegram_user_id, now)


def draw(session: Session, player: Player, now: datetime, rng: random.Random = _RNG) -> Draw:
    season, run = _resolve(session)
    day = local_day(now, get_group_timezone(session))
    if drawn_today(session, season.id, player.telegram_user_id, day) is not None:
        raise _refuse(OmikujiRefusal.ALREADY)
    outcome = pick(run.omikuji, rng)
    row = OmikujiDraw(
        season_id=season.id,
        player_id=player.telegram_user_id,
        local_date=day,
        outcome=outcome.kind.value,
        drawn_at=naive_utc(now),
    )
    _insert_row(session, row)
    _pay(session, player, season, run, outcome)
    prank = _prank(session, player, run, now) if outcome.prank else None
    spared = outcome.prank and prank is None
    fields = {
        "season_id": season.id,
        "outcome": outcome.kind.value,
        "coins": outcome.coins,
        "consumable": None if outcome.consumable is None else outcome.consumable.value,
        "hex_kind": None if prank is None else prank.kind,
        "spared": spared,
    }
    events.emit(
        session,
        EventType.OMIKUJI_DRAWN,
        events.Involved(actor_id=player.telegram_user_id),
        **fields,
    )
    logger.info("drew omikuji {outcome}", **fields)
    return Draw(run=run, outcome=outcome, local_date=day, prank=prank, spared=spared)


def slip_png(run_id: str, kind: OmikujiKind) -> bytes | None:
    path = SEASON_ART_DIR / run_id / "omikuji" / f"{kind.value}.png"
    try:
        return path.read_bytes()
    except OSError:
        logger.error("omikuji slip art missing: {path}", path=str(path), run_id=run_id)
        return None
