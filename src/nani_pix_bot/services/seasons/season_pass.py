"""The Season Pass (seasons spec §2): tiers by Season XP, free rewards for
everyone, premium ones with Premium, each granted once and in order as XP
reaches it."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import ConsumableKind, CurrencyReason, ItemSource, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonPass, SeasonSchedule, SeasonXp
from nani_pix_bot.seasons.definition import Reward, RewardKind, SeasonRun
from nani_pix_bot.services import players
from nani_pix_bot.services.cosmetics import consumables, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet

GRANTING = (SeasonStatus.ACTIVE, SeasonStatus.CLOSING)


@dataclass(frozen=True)
class Granted:
    tier: int  # 1-based
    premium: bool
    reward: Reward
    new: bool = True  # False: an item the player already owned


@dataclass(frozen=True)
class _Track:
    season: SeasonSchedule
    run: SeasonRun
    player: Player
    progress: SeasonPass
    premium: bool


def reached(run: SeasonRun, xp_total: int) -> int:
    return sum(1 for tier in run.tiers if xp_total >= tier.xp)


def xp_total(session: Session, season_id: int, player_id: int) -> int:
    stmt = select(func.coalesce(func.sum(SeasonXp.amount), 0)).where(
        SeasonXp.season_id == season_id, SeasonXp.player_id == player_id
    )
    return int(session.scalar(stmt) or 0)


def row(session: Session, season_id: int, player_id: int) -> SeasonPass:
    found = session.get(SeasonPass, (season_id, player_id))
    if found is None:
        found = SeasonPass(
            season_id=season_id, player_id=player_id, free_claimed=0, premium_claimed=0
        )
        session.add(found)
        session.flush()
    return found


def _give(
    session: Session, season: SeasonSchedule, run: SeasonRun, player: Player, reward: Reward
) -> bool:
    """Hand one reward over; False only for an item the player already owned."""
    if reward.kind is RewardKind.ITEM:
        request = ItemGrant(f"{run.run_id}/{reward.item_key}", ItemSource.TIER, season.id)
        return inventory.grant(session, player.telegram_user_id, request)
    if reward.kind is RewardKind.COINS:
        entry = wallet.LedgerEntry(CurrencyReason.TIER_REWARD, season_id=season.id, coins=True)
        wallet.credit(session, player, reward.amount, entry)
    elif reward.kind is RewardKind.CURRENCY:
        entry = wallet.LedgerEntry(CurrencyReason.TIER_REWARD, season_id=season.id)
        wallet.credit(session, player, reward.amount, entry)
    elif reward == Reward.clue_ticket():
        consumables.add(
            session, player.telegram_user_id, ConsumableKind.CLUE_TICKET, why=f"{run.run_id} pass"
        )
    else:
        raise ValueError(f"unknown pass reward {reward!r}")
    return True


def _grant_track(session: Session, track: _Track, level: int) -> list[Granted]:
    claimed = track.progress.premium_claimed if track.premium else track.progress.free_claimed
    granted = []
    for number in range(claimed + 1, level + 1):
        tier = track.run.tiers[number - 1]
        reward = tier.premium if track.premium else tier.free
        if reward is not None:
            new = _give(session, track.season, track.run, track.player, reward)
            granted.append(Granted(number, track.premium, reward, new))
            logger.info(
                "pass tier {tier} ({track}) granted to {recipient}: {reward}{owned}",
                tier=number,
                track="premium" if track.premium else "free",
                recipient=players.describe_player_id(session, track.player.telegram_user_id),
                recipient_id=track.player.telegram_user_id,
                reward=reward.kind.value,
                owned="" if new else " (already owned)",
                season_id=track.season.id,
                run_id=track.run.run_id,
            )
    if track.premium:
        track.progress.premium_claimed = max(claimed, level)
    else:
        track.progress.free_claimed = max(claimed, level)
    session.flush()
    return granted


def sync(session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int) -> list[Granted]:
    """Grant every tier `player_id`'s Season XP now reaches and they haven't had:
    the free track always, the premium track once Premium is bought. Nothing
    once the season is over (its podium and ledger are frozen)."""
    if season.status not in GRANTING:
        return []
    player = session.get(Player, player_id)
    if player is None:
        return []
    progress = row(session, season.id, player_id)
    level = reached(run, xp_total(session, season.id, player_id))
    granted = _grant_track(session, _Track(season, run, player, progress, False), level)
    if progress.premium_transfer_id is not None:
        granted += _grant_track(session, _Track(season, run, player, progress, True), level)
    return sorted(granted, key=lambda g: (g.tier, g.premium))
