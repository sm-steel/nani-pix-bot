"""/candles — the season's 100 Candles goal and the viewer's bones (Halloween).

The reply is the candle-counter card with the progress lines as its caption;
the text alone when the art is missing or the card can't be sent. Works in the
game topic and in a DM (group members only); anywhere else it is ignored."""

import asyncio

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import GroupGoal
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cards import GoalCard, render_goal_card
from nani_pix_bot.services.seasons import bones, group_goal, schedule

Found = tuple[SeasonSchedule, SeasonRun, GroupGoal]


def _next_milestone(snuffed: int, goal: GroupGoal) -> int | None:
    """The next milestone post below the target, if one is left."""
    every = goal.milestone_every
    if every <= 0:
        return None
    milestone = (snuffed // every + 1) * every
    return milestone if milestone < goal.target else None


def _weights(run: SeasonRun, goal: GroupGoal, lang: str) -> str | None:
    if run.event_day is None:
        return None
    return i18n.t(
        "candles.weights",
        lang,
        game=goal.per_game,
        haunted=goal.per_haunted,
        event=run.event_day.names.get(lang) or run.event_day.names["EN"],
        day=goal.per_event_day,
    )


def _boss_line(session: Session, boss: Game | None, lang: str) -> str:
    """The Gashadokuro's state once the goal is reached."""
    if boss is None:
        return i18n.t("candles.boss_pending", lang)
    if boss.status == GameStatus.WON:
        if boss.winner_id is None:
            return i18n.t("candles.boss_won_nobody", lang, game_id=boss.id)
        winner = players.display_name(session, boss.winner_id)
        return i18n.t("candles.boss_won", lang, game_id=boss.id, winner=winner)
    if boss.status == GameStatus.UNSOLVED:
        return i18n.t("candles.boss_unsolved", lang, game_id=boss.id)
    return i18n.t("candles.boss_running", lang, game_id=boss.id)


def candles_text(session: Session, found: Found, counted: tuple[int, int], lang: str) -> str:
    """The caption lines; `counted` is (candles snuffed, viewer id), so the count is
    read once per call."""
    season, run, goal = found
    snuffed, viewer_id = counted
    settled = group_goal.is_settled(session, season.id)
    lines = [
        i18n.t("candles.title", lang, season=run.name(lang)),
        i18n.t(
            f"candles.progress.{i18n.plural(snuffed, lang)}",
            lang,
            snuffed=snuffed,
            target=goal.target,
        ),
        _weights(run, goal, lang),
    ]
    milestone = None if settled else _next_milestone(snuffed, goal)
    if milestone is not None:
        lines.append(i18n.t("candles.next", lang, milestone=milestone))
    if settled:
        lines.append(_boss_line(session, group_goal.boss_game(session, season.id), lang))
    if group_goal.is_contributor(session, season.id, viewer_id):
        lines.append(i18n.t("candles.you_helped", lang))
    elif not settled:
        lines.append(i18n.t("candles.you_not_yet", lang))
    if run.bone_set is not None:
        owned = bones.count(session, season.id, viewer_id)
        lines.append(i18n.t("candles.bones", lang, bones=owned, target=run.bone_set.bones))
    return "\n".join(line for line in lines if line is not None)


async def _send(message: Message, run_id: str, snuffed: int, target: int, text: str) -> None:
    try:
        card = await asyncio.to_thread(render_goal_card, GoalCard(run_id, snuffed, target))
    except OSError:
        logger.warning("no candle-counter art for {run_id} — sending text", run_id=run_id)
    else:
        try:
            await message.reply_photo(photo=card, caption=text)
            return
        except TelegramError as error:
            logger.warning(
                "could not send the candle card — sending text: {error}", error=str(error)
            )
    await message.reply_text(text)


async def candles_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    bot_data = context.bot_data
    if is_private_chat(update):
        refusal = await outsider_refusal(context, user.id, "candles")
        if refusal is not None:
            await message.reply_text(refusal)
            return
    elif not is_game_topic(
        update, group_chat_id=bot_data["group_chat_id"], game_topic_id=bot_data["game_topic_id"]
    ):
        return
    with session_scope(bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        season = schedule.current(session)
        found = group_goal.goal_of(session, season.id if season is not None else None)
        if found is None:
            text, run_id, snuffed, target = i18n.t("candles.none", lang), None, 0, 0
        else:
            _, run, goal = found
            snuffed = group_goal.candles(session, found[0].id, goal)
            text = candles_text(session, found, (snuffed, user.id), lang)
            run_id, target = run.run_id, goal.target
    logger.info("viewed /candles", snuffed=snuffed, target=target)
    if run_id is None:
        await message.reply_text(text)
        return
    await _send(message, run_id, snuffed, target, text)
