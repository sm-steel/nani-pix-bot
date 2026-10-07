"""Evaluates achievements as events arrive (spec §3). Telegram-free."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementClaim, AchievementGrant
from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import players, settings
from nani_pix_bot.services.achievements import catalogue, outbox, rewards
from nani_pix_bot.services.achievements.definitions import (
    Definition,
    Kind,
    exact_tier,
    rarity_of,
    reached,
)
from nani_pix_bot.services.achievements.history import DbHistory
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.events import LoggedEvent


@dataclass(frozen=True)
class GrantRequest:
    player_id: int
    key: str
    tier: int = 1
    period_key: str = ""


def held_tiers(session: Session, player_id: int, key: str, period_key: str = "") -> set[int]:
    stmt = select(AchievementGrant.tier).where(
        AchievementGrant.player_id == player_id,
        AchievementGrant.key == key,
        AchievementGrant.period_key == period_key,
    )
    return set(session.scalars(stmt))


def _pay(session: Session, row: AchievementGrant) -> None:
    """Runs after the grant row exists, so a reward that cascades into
    another unlock (Pixel Magnate) is recorded — and announced — after it."""
    player = session.get(Player, row.player_id)
    if row.reward <= 0:
        return
    if player is None:
        logger.warning(
            "{achievement} tier {tier} reward of {reward} 💠 not paid: no player row",
            achievement=row.key,
            tier=row.tier,
            reward=row.reward,
            player_id=row.player_id,
        )
        return
    entry = wallet.LedgerEntry(CurrencyReason.ACHIEVEMENT)
    transfer = wallet.credit(session, player, row.reward, entry)
    session.flush()
    row.transfer_id = transfer.id


def grant(
    session: Session, request: GrantRequest, *, batch_id: int | None = None
) -> AchievementGrant:
    defn = catalogue.get(request.key)
    rarity = rarity_of(defn, request.tier)
    row = AchievementGrant(
        player_id=request.player_id,
        key=request.key,
        tier=request.tier,
        period_key=request.period_key,
        rarity=rarity,
        reward=rewards.reward(session, rarity),
        points=rewards.POINTS[rarity],
    )
    session.add(row)
    session.flush()
    logger.info(
        "{player} unlocked {achievement} tier {tier} ({rarity}, +{reward} 💠, +{points} pts)",
        player=players.describe_player_id(session, request.player_id),
        player_id=request.player_id,
        achievement=request.key,
        tier=request.tier,
        rarity=rarity.value,
        reward=row.reward,
        points=row.points,
        period=request.period_key or None,
        batch_id=batch_id,
    )
    outbox.enqueue_unlock(session, row.id, batch_id)
    _pay(session, row)
    return row


def _claim(session: Session, key: str, tier: int, player_id: int) -> bool:
    taken = session.get(AchievementClaim, (key, tier))
    if taken is not None:
        logger.debug(
            "{key} tier {tier} was already claimed by {holder_id}",
            key=key,
            tier=tier,
            holder_id=taken.player_id,
        )
        return False
    session.add(AchievementClaim(key=key, tier=tier, player_id=player_id))
    session.flush()
    return True


def _evaluate_group_unique(
    session: Session, defn: Definition, history: DbHistory, event: LoggedEvent
) -> list[AchievementGrant]:
    if event.event_type is not EventType.GAME_WON or history.player_id != event.actor_id:
        return []
    tier = exact_tier(defn, defn.progress(history))
    if tier is None or not _claim(session, defn.key, tier, history.player_id):
        return []
    return [grant(session, GrantRequest(history.player_id, defn.key, tier), batch_id=event.id)]


def _evaluate(
    session: Session, defn: Definition, history: DbHistory, event: LoggedEvent
) -> list[AchievementGrant]:
    if defn.kind is Kind.GROUP_UNIQUE:
        return _evaluate_group_unique(session, defn, history, event)
    top = reached(defn, defn.progress(history))
    granted: list[AchievementGrant] = []
    for tier in range(1, top + 1):
        # Re-read every time: paying a reward re-enters the engine through
        # the emitted currency_moved and may already have granted this tier.
        if tier not in held_tiers(session, history.player_id, defn.key):
            request = GrantRequest(history.player_id, defn.key, tier)
            granted.append(grant(session, request, batch_id=event.id))
    return granted


def _ignored(event: LoggedEvent) -> bool:
    """Spec §2: refunds and chargebacks never trigger anything."""
    return event.event_type is EventType.CURRENCY_MOVED and bool(event.data.get("reversal"))


def _involved(session: Session, event: LoggedEvent) -> list[int]:
    ids = dict.fromkeys(p for p in (event.actor_id, event.subject_id) if p is not None)
    return [p for p in ids if session.get(Player, p) is not None]


def on_event(session: Session, event: LoggedEvent) -> list[AchievementGrant]:
    triggered = catalogue.triggered_by(event.event_type)
    if not triggered or _ignored(event):
        return []
    tz = settings.get_group_timezone(session)
    granted: list[AchievementGrant] = []
    for player_id in _involved(session, event):
        history = DbHistory(session, player_id, tz)
        for defn in triggered:
            granted += _evaluate(session, defn, history, event)
    return granted
