"""Building and sending the group post for a stage/game outcome, shared by
/guess and /sharpen. Everything here prepares plain values inside the caller's
session; the send happens after that session commits."""

from dataclasses import dataclass, replace

from loguru import logger
from telegram import Message
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.jobs.timers.current_image import RevealTarget
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


@dataclass(frozen=True)
class PostedLog:
    """The INFO line logged once a stage post is confirmed sent, e.g.
    "posted stage 2/5 image (320px wide)" — says which stage, which the
    generic post helpers in jobs/timers/current_image.py can't know."""

    stage: str
    game_id: int
    width: int | None = None


@dataclass(frozen=True)
class Announcement:
    """What a WON/STAGE_ADVANCED/TURN_ADVANCED/UNSOLVED outcome needs to
    post once its session has committed — captured as plain values (not
    the ORM object) since the announcement happens after that session
    closes.

    Exactly one of `photo`/`photos` is populated: a normal-mode outcome
    sets `photo` (sent via post_current_image), a hard-mode outcome sets
    `photos` (sent via post_current_images as a 2-photo album) — see
    `send_announcement` in this module, which picks between the two
    functions based on which field is set. A non-stage ending that sets
    `reveal` goes through post_reveal/post_reveal_pair instead, so the
    animated reveal video is posted."""

    caption: str
    photo: bytes | None = None
    photos: tuple[bytes, bytes] | None = None
    is_stage_post: bool = False
    posted_log: PostedLog | None = None
    reveal: RevealTarget | None = None


def require_original_image(game: Game, situation: str) -> bytes:
    """Restores the type narrowing lost across guess_command's WON/
    STAGE_ADVANCED/UNSOLVED branches for original_image, converting the
    old bare `assert` at each site to a real exception (S101, issue
    #117) so it can't silently vanish under `python -O`. Hoisted out of
    guess_command into its own function (rather than an inline
    `if ... raise` at each of the three call sites) purely to keep
    guess_command's own cyclomatic complexity under qlty's threshold —
    see CLAUDE.md's Tooling section.

    original_image is deliberately NOT checked/loaded any earlier than
    this: it's a deferred column (see models/game.py), and a WRONG
    guess — by far the most common outcome — never reads it. Checking
    it up front would force-load the blob on every single /guess; each
    branch that actually needs the bytes calls this once, right where
    it's used."""
    if game.original_image is None:
        raise RuntimeError(f"game.original_image is None on {situation}")
    return game.original_image


def prepare_stage_advanced_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> Announcement:
    # guess_command already checked this is set — restores the type
    # narrowing lost by passing `game` across a function boundary, same
    # as require_original_image does for original_image below.
    if game.current_stage is None:
        raise RuntimeError("game.current_stage is None on a STAGE_ADVANCED outcome")
    original_bytes = require_original_image(game, "a STAGE_ADVANCED outcome")
    target_width = stage_config.get_stage_config(session, game.current_stage).target_width
    pixelated = pixelate_service.pixelate(original_bytes, target_width, game.pixel_algorithm)
    progress = game_service.stage_progress(session, game)
    game_service.reset_inactivity_clock(session, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    caption = i18n.t(
        "guess.stage_advanced_caption",
        lang,
        stage=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
    )
    return Announcement(
        photo=pixelated,
        caption=caption,
        is_stage_post=True,
        posted_log=PostedLog(
            stage=game_service.stage_label(game.current_stage),
            game_id=game.id,
            width=target_width,
        ),
    )


async def send_announcement(
    context: ContextTypes.DEFAULT_TYPE, session_factory, announcement: Announcement
) -> Message | tuple[Message, ...] | None:
    """Picks post_current_image vs. post_current_images based on which
    of Announcement's photo/photos fields is populated, and sends it.
    Hoisted out of guess_command purely to keep its own cyclomatic
    complexity down, same reasoning as require_original_image above."""
    sent = await _send(context, session_factory, announcement)
    if sent is not None and announcement.posted_log is not None:
        _log_posted(announcement.posted_log)
    return sent


def _log_posted(posted: PostedLog) -> None:
    if posted.width is None:
        logger.info("posted {stage} images", stage=posted.stage, game_id=posted.game_id)
    else:
        logger.info(
            "posted {stage} image ({width}px wide)",
            stage=posted.stage,
            width=posted.width,
            game_id=posted.game_id,
        )


async def _send_reveal(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    announcement: Announcement,
    reveal: RevealTarget,
) -> Message | tuple[Message, ...] | None:
    if announcement.photos is not None:
        return await timeout_module.post_reveal_pair(
            context,
            session_factory,
            target=reveal,
            photos=announcement.photos,
            caption=announcement.caption,
        )
    if announcement.photo is not None:
        return await timeout_module.post_reveal(
            context,
            session_factory,
            target=reveal,
            photo=announcement.photo,
            caption=announcement.caption,
        )
    raise RuntimeError("Announcement has neither photo nor photos set")


async def _send(
    context: ContextTypes.DEFAULT_TYPE, session_factory, announcement: Announcement
) -> Message | tuple[Message, ...] | None:
    if announcement.reveal is not None and not announcement.is_stage_post:
        return await _send_reveal(context, session_factory, announcement, announcement.reveal)
    if announcement.photos is not None:
        post_album = (
            timeout_module.post_stage_images
            if announcement.is_stage_post
            else timeout_module.post_current_images
        )
        return await post_album(
            context, session_factory, photos=announcement.photos, caption=announcement.caption
        )
    if announcement.photo is not None:
        post_photo = (
            timeout_module.post_stage_image
            if announcement.is_stage_post
            else timeout_module.post_current_image
        )
        return await post_photo(
            context, session_factory, photo=announcement.photo, caption=announcement.caption
        )
    raise RuntimeError("Announcement has neither photo nor photos set")


def with_suffix(announcement: Announcement | None, suffix: str) -> Announcement | None:
    """Appends the currency-earnings lines to an outcome's group caption."""
    if announcement is None or not suffix:
        return announcement
    return replace(announcement, caption=announcement.caption + suffix)
