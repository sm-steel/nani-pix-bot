"""The 100 Candles posts: a milestone, the goal reached and the Gashadokuro's
rise (counter card or boss picture in the game topic), and a player's bone DM."""

from collections.abc import Callable

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.jobs import season_posts
from nani_pix_bot.jobs.post import Post
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import BoneSet, GroupGoal
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cards import GoalCard

Renderer = Callable[[Session, AnnouncementOutbox, str], Post | None]


def _item_name(run: SeasonRun, key: str, lang: str) -> str:
    return next(item.name(lang) for item in run.items if item.key == key)


def _goal(
    session: Session, row: AnnouncementOutbox, kind: OutboxKind
) -> tuple[SeasonRun, GroupGoal] | None:
    found = season_posts.open_season(session, row, kind)
    if found is None or found[1].group_goal is None:
        return None
    return found[1], found[1].group_goal


def _reward(goal: GroupGoal, run: SeasonRun, lang: str) -> str:
    coins, key = goal.contributor_coins, goal.contributor_item_key
    if key is None:
        return i18n.t("goal.reward.coins", lang, coins=coins)
    item = _item_name(run, key, lang)
    if not coins:
        return i18n.t("goal.reward.item", lang, item=item)
    return i18n.t("goal.reward.both", lang, coins=coins, item=item)


def _milestone_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    found = _goal(session, row, OutboxKind.GOAL_MILESTONE)
    if found is None:
        return None
    run, goal = found
    snuffed = row.payload["milestone"]
    text = i18n.t("goal.post.milestone", lang, snuffed=snuffed, target=goal.target)
    return Post((row.id,), text, GoalCard(run.run_id, snuffed, goal.target))


def _reached_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    found = _goal(session, row, OutboxKind.GOAL_REACHED)
    if found is None:
        return None
    run, goal = found
    players = row.payload["players"]
    text = i18n.t(
        f"goal.post.reached.{i18n.plural(players, lang)}",
        lang,
        target=goal.target,
        players=players,
        reward=_reward(goal, run, lang),
    )
    return Post((row.id,), text, GoalCard(run.run_id, goal.target, goal.target))


def _boss_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    found = _goal(session, row, OutboxKind.GOAL_BOSS)
    if found is None:
        return None
    run, goal = found
    game_id = row.payload["game_id"]
    game = session.get(Game, game_id)
    if game is None or not game.boss_game:
        logger.info(
            "skipped the boss post: game {boss_id} is gone or no longer the boss game",
            boss_id=game_id,
            season_id=row.payload["season_id"],
        )
        return None
    text = i18n.t(
        "goal.post.boss",
        lang,
        game_id=game_id,
        xp=goal.boss_xp_multiplier,
        coins=goal.boss_coin_multiplier,
    )
    return Post((row.id,), text, GoalCard(run.run_id, goal.target, goal.target, boss=True))


def _bone_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    found = season_posts.open_season(session, row, OutboxKind.BONE_FOUND)
    bones: BoneSet | None = found[1].bone_set if found is not None else None
    if found is None or bones is None:
        return None
    run = found[1]
    if row.payload["granted"]:
        item = _item_name(run, bones.item_key, lang)
        text = i18n.t("goal.bone.complete", lang, target=bones.bones, item=item)
    else:
        text = i18n.t("goal.bone.found", lang, bones=row.payload["bones"], target=bones.bones)
    return Post((row.id,), text, dm_to=row.payload["player_id"])


RENDERERS: dict[OutboxKind, Renderer] = {
    OutboxKind.GOAL_MILESTONE: _milestone_post,
    OutboxKind.GOAL_REACHED: _reached_post,
    OutboxKind.GOAL_BOSS: _boss_post,
    OutboxKind.BONE_FOUND: _bone_post,
}
