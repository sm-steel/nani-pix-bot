"""/status — a manual resync for the game topic (issue #141). When the chat
seems out of step with the bot (a send that never arrived, a missing or
stale pin), it re-posts the current stage image with a live caption:
stage, guesses left at it, the bounty, the time until the game ends on its
own. With no game running it says what is happening instead (a setup, a
vote, whose turn it is). It never changes state and never re-pins."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InputMediaPhoto, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus, PixelAlgorithm
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.economy import bounty
from nani_pix_bot.services.game import turns
from nani_pix_bot.services.settings import stage_config


@dataclass(frozen=True)
class LiveStatus:
    """Where the running game stands, as plain values for the caption."""

    game_id: int
    hard_mode: bool
    number: int  # stage, or HARD MODE turn
    total: int
    remaining: int
    limit: int
    pot: int
    time_left: timedelta | None


@dataclass(frozen=True)
class _Repost:
    live: LiveStatus
    images: tuple[bytes, ...]  # one, or the HARD MODE pair
    width: int
    algorithm: PixelAlgorithm


def caption(live: LiveStatus, lang: str) -> str:
    key = "status.turn_hard" if live.hard_mode else "status.stage"
    lines = [
        i18n.t(
            key,
            lang,
            stage=live.number,
            total=live.total,
            remaining=live.remaining,
            limit=live.limit,
        )
    ]
    if live.pot > 0:
        lines.append(i18n.t("bounty.caption_line", lang, amount=live.pot))
    if live.time_left is not None and live.time_left > timedelta(0):
        lines.append(i18n.t("status.ends", lang, duration=duration(live.time_left, lang)))
    lines.append(i18n.t("game.id_line", lang, id=live.game_id))
    return "\n".join(lines)


def _time_left(game: Game, now: datetime) -> timedelta | None:
    end = game.scheduled_end_at
    if end is None:
        return None
    return (end.replace(tzinfo=UTC) if end.tzinfo is None else end) - now


def _repost(session: Session, game: Game, now: datetime) -> _Repost | None:
    """The running game's image(s) and status; None if they're missing."""
    if game.hard_mode:
        if not game_service.has_hard_mode_reveal_images(game):
            return None
        progress = game_service.hard_mode_turn_progress(game)
        images = game_service.hard_mode_reveal_images(game)
        width = game_service.hard_mode_turn_width(game)
    else:
        if game.original_image is None or game.current_stage is None:
            return None
        progress = game_service.stage_progress(session, game)
        images = (game.original_image,)
        width = stage_config.get_stage_config(session, game.current_stage).target_width
    live = LiveStatus(
        game_id=game.id,
        hard_mode=game.hard_mode,
        number=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
        pot=bounty.pot_balance(session, game.id),
        time_left=_time_left(game, now),
    )
    return _Repost(live, tuple(images), width, game.pixel_algorithm)


def _idle_text(session: Session, game: Game | None, lang: str) -> str:
    if game is not None and game.status is GameStatus.SETUP:
        starter = players.display_name(session, game.starter_id)
        return i18n.t("status.setup", lang, starter=starter)
    if game is not None and game.status is GameStatus.VOTING:
        return i18n.t("status.voting", lang, id=game.id)
    state = turns.get_turn_state(session)
    if state is not None and state.next_starter_id is not None:
        player = players.display_name(session, state.next_starter_id)
        return i18n.t("status.turn_player", lang, player=player)
    return i18n.t("status.turn_open", lang)


def _render(repost: _Repost) -> list[bytes]:
    return [
        pixelate_service.pixelate(image, repost.width, repost.algorithm) for image in repost.images
    ]


async def _send(context: ContextTypes.DEFAULT_TYPE, repost: _Repost, lang: str) -> None:
    where = {
        "chat_id": context.bot_data["group_chat_id"],
        "message_thread_id": context.bot_data["game_topic_id"],
    }
    photos = await asyncio.to_thread(_render, repost)
    text = caption(repost.live, lang)
    try:
        if len(photos) == 1:
            await context.bot.send_photo(photo=photos[0], caption=text, **where)
        else:
            media = [InputMediaPhoto(photos[0], caption=text)]
            media += [InputMediaPhoto(photo) for photo in photos[1:]]
            await context.bot.send_media_group(media=media, **where)
    except TelegramError as exc:
        logger.warning(
            "couldn't re-post the current stage via /status: {error}",
            error=exc,
            game_id=repost.live.game_id,
        )
        return
    logger.info(
        "re-posted the current {unit} {number}/{total} via /status",
        unit="turn" if repost.live.hard_mode else "stage",
        number=repost.live.number,
        total=repost.live.total,
        game_id=repost.live.game_id,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    ):
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        game = game_service.active_or_setup_game(session)
        repost = None
        if game is not None and game.status is GameStatus.ACTIVE:
            repost = _repost(session, game, datetime.now(UTC))
        idle = _idle_text(session, game, lang) if repost is None else ""
    if repost is not None:
        await _send(context, repost, lang)
        return
    logger.info("answered /status with no image to show: {text!r}", text=idle)
    await message.reply_text(idle)
