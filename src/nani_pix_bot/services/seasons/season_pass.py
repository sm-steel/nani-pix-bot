"""The Season Pass (seasons spec §2): tiers by Season XP, free rewards for
everyone, premium ones with Premium, each granted once and in order as XP
reaches it."""

import enum
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import (
    ConsumableKind,
    CurrencyReason,
    ItemSource,
    SeasonStatus,
)
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonPass, SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import CONSUMABLE_REWARDS, Reward, RewardKind, SeasonRun
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cosmetics import consumables, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import pass_completion

GRANTING = (SeasonStatus.ACTIVE, SeasonStatus.CLOSING)
# Nothing to deliver: the profile reads the claimed tier count. _grant_track's
# "granted to {recipient}" line is the one log of these.
_CLAIM_ONLY = frozenset({RewardKind.STAMP, RewardKind.STAMP_PLUS})


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
    elif reward.kind in CONSUMABLE_REWARDS:
        kind = ConsumableKind(reward.kind.value)
        consumables.add(session, player.telegram_user_id, kind, why=f"{run.run_id} pass")
    elif reward.kind not in _CLAIM_ONLY:
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
    before = pass_completion.done(progress, run)
    granted = _grant_track(session, _Track(season, run, player, progress, False), level)
    if progress.premium_transfer_id is not None:
        granted += _grant_track(session, _Track(season, run, player, progress, True), level)
    granted.sort(key=lambda g: (g.tier, g.premium))
    if granted:
        entries = [{"tier": g.tier, "premium": g.premium, "new": g.new} for g in granted]
        outbox.enqueue_tier(session, season.id, player_id, entries)
    pass_completion.announce(session, season, run, progress, before)
    return granted


def notice_text(run: SeasonRun, granted: list[dict], lang: str) -> str:
    """The DM listing every reward a sync granted (`granted` as queued by
    outbox.enqueue_tier); an owned item is marked as already the player's."""
    lines = [i18n.t("season_pass.notice.header", lang, season=run.name(lang))]
    for g in granted:
        tier = run.tiers[g["tier"] - 1]
        reward = tier.premium if g["premium"] else tier.free
        if reward is None:
            continue
        key = "season_pass.notice.line_premium" if g["premium"] else "season_pass.notice.line"
        text = describe(run, reward, lang)
        if not g["new"]:
            text = i18n.t("season_pass.notice.already_owned", lang, reward=text)
        lines.append(i18n.t(key, lang, tier=g["tier"], reward=text))
    lines.append(i18n.t("season_pass.notice.footer", lang))
    return "\n".join(lines)


def describe(run: SeasonRun, reward: Reward, lang: str) -> str:
    """One reward in player words."""
    if reward.kind is RewardKind.ITEM:
        found = registry.item(f"{run.run_id}/{reward.item_key}")
        name = found.item.name(lang) if found else reward.item_key
        slot = i18n.t(f"cosmetics.slot.{found.item.slot.value}", lang) if found else ""
        return i18n.t("season_pass.reward.item", lang, slot=slot, name=name)
    if reward.kind is RewardKind.COINS:
        return f"{reward.amount} 🪙"
    if reward.kind is RewardKind.CURRENCY:
        return f"{reward.amount} 💠"
    # clue_token, stamp and stamp_plus: the text key is the kind's value
    return i18n.t(f"season_pass.reward.{reward.kind.value}", lang)


class PremiumRefusal(enum.StrEnum):
    NOT_OPEN = "not_open"
    ALREADY = "already"
    NO_PREMIUM = "no_premium"
    INSUFFICIENT = "insufficient"


class PremiumRefusedError(Exception):
    def __init__(self, refusal: PremiumRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def _premium_refusal(
    season: SeasonSchedule, run: SeasonRun, progress: SeasonPass
) -> PremiumRefusal | None:
    if season.status != SeasonStatus.ACTIVE:
        return PremiumRefusal.NOT_OPEN
    if not any(tier.premium for tier in run.tiers):
        return PremiumRefusal.NO_PREMIUM
    if progress.premium_transfer_id is not None:
        return PremiumRefusal.ALREADY
    return None


def buy_premium(
    session: Session, season: SeasonSchedule, run: SeasonRun, player: Player
) -> list[Granted]:
    """Charge `run.premium_price` 💠 for Premium (only while the season is ACTIVE) and
    grant every premium tier the player's XP already reached."""
    progress = row(session, season.id, player.telegram_user_id)
    refusal = _premium_refusal(season, run, progress)
    if refusal is not None:
        logger.warning(
            "refused Premium for {run_id}: {reason}",
            run_id=run.run_id,
            reason=refusal.value,
            season_id=season.id,
        )
        raise PremiumRefusedError(refusal)
    entry = wallet.LedgerEntry(CurrencyReason.PREMIUM, season_id=season.id)
    try:
        charge = wallet.debit(session, player, run.premium_price, entry)
    except wallet.InsufficientCurrencyError as error:
        logger.warning(
            "refused Premium for {run_id}: {reason}",
            run_id=run.run_id,
            reason=PremiumRefusal.INSUFFICIENT.value,
            season_id=season.id,
        )
        raise PremiumRefusedError(PremiumRefusal.INSUFFICIENT) from error
    session.flush()
    progress.premium_transfer_id = charge.id
    logger.info(
        "bought Premium for {run_id} ({price} 💠)",
        run_id=run.run_id,
        price=run.premium_price,
        season_id=season.id,
    )
    return sync(session, season, run, player.telegram_user_id)
