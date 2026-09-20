"""The 2-day absolute game timeout — see MECHANICS.md's "Timeout"
section."""

from dataclasses import dataclass
from typing import cast

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until_timeout
from nani_pix_bot.jobs.timers.current_image import (
    clear_image_if_sent,
    post_current_image,
    post_current_images,
)
from nani_pix_bot.jobs.timers.inactivity import cancel_inactivity_timers
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings


def timeout_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's timeout — lets this
    module look up and cancel a pending job (e.g. on a win) or re-arm it
    on startup without storing anything extra on the row."""
    return f"game-timeout-{game_id}"


def schedule_timeout(job_queue: JobQueue | None, game: Game) -> None:
    if job_queue is None:
        return
    delay = seconds_until_timeout(game)
    logger.debug("Scheduling 2-day timeout for game {} in {:.0f}s", game.id, delay)
    job_queue.run_once(
        timeout_job_callback,
        when=delay,
        name=timeout_job_name(game.id),
        data=game.id,
    )


def cancel_timeout(job_queue: JobQueue | None, game_id: int) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling 2-day timeout for game {}", game_id)
    for job in job_queue.get_jobs_by_name(timeout_job_name(game_id)):
        job.schedule_removal()


@dataclass(frozen=True)
class _HardModeTimeoutReveal:
    """What a hard-mode game's timeout ending needs to post via
    post_current_images once its session has committed — the hard-mode
    analogue of the normal path's single original_bytes/caption pair
    below, carrying a screenshot pair instead of one photo."""

    photos: tuple[bytes, bytes]
    caption: str


def _hard_mode_timeout_reveal(game: Game, lang: str) -> _HardModeTimeoutReveal:
    """Both stored hard-mode screenshots, unpixelated, with a
    hard-mode-specific timeout caption — the hard-mode analogue of the
    normal path's `game.original_image` + `timeout.caption` reveal."""
    photos = game_service.hard_mode_reveal_images(game)
    caption = i18n.t(
        "timeout.hard_mode_caption", lang, title=game_service.display_title(game, lang)
    )
    return _HardModeTimeoutReveal(photos=photos, caption=caption)


async def timeout_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 2 days after a game started with no ending yet. A
    hard-mode game (always `original_image is None`) is still eligible
    as long as it still has both its stored screenshots — see
    has_hard_mode_reveal_images() and _hard_mode_timeout_reveal above.

    Imports maybe_overthrow lazily (function-local, not at module level):
    autostart.py already imports schedule_timeout from this module, so a
    module-level import the other way round would be a real circular
    import, not just the theoretical shape the sibling-import convention
    is meant to dodge — Python's import system can't resolve two modules
    that both need each other fully initialized at parse time. See
    turn_timers.py's turn_expiry_job_callback for the identical
    situation with schedule_idle_autostart."""
    from nani_pix_bot.jobs.timers.autostart import maybe_overthrow

    job = context.job
    if job is None:
        return
    game_id = cast(int, job.data)

    session_factory = context.bot_data["session_factory"]
    hard_mode_reveal: _HardModeTimeoutReveal | None = None
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = session.get(Game, game_id)
        if (
            game is None
            or game.status != GameStatus.ACTIVE
            or (game.original_image is None and not game_service.has_hard_mode_reveal_images(game))
        ):
            logger.debug("Timeout fired for game {} but it's already resolved — no-op", game_id)
            return

        logger.info("Game {} timed out after 2 days — ending unsolved", game_id)
        game_service.force_unsolved(game)
        game_service.mark_turn_open_if_unassigned(session)
        cancel_inactivity_timers(context.job_queue, game.id)
        if game.hard_mode:
            hard_mode_reveal = _hard_mode_timeout_reveal(game, lang)
        else:
            original_bytes = game.original_image
            caption = i18n.t("timeout.caption", lang, title=game_service.display_title(game, lang))
    # Block closed and committed above — the UNSOLVED ending is durable
    # now regardless of whether the announcement below actually reaches
    # the group (see post_current_image's docstring).
    if hard_mode_reveal is not None:
        sent = await post_current_images(
            context,
            session_factory,
            photos=hard_mode_reveal.photos,
            caption=hard_mode_reveal.caption,
        )
    else:
        sent = await post_current_image(
            context, session_factory, photo=original_bytes, caption=caption
        )
    clear_image_if_sent(session_factory, game_id, sent)
    await maybe_overthrow(context, session_factory)
