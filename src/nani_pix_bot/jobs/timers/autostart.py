"""Bot-initiated game starting: the 24h idle-autostart timer, and the
probabilistic "overthrow" trigger fired right after a game concludes —
see the design behind issue #159. Builds on
services/game/autostart.py's pure picking logic; this module owns the
DB write, JobQueue scheduling, and Telegram posting around it."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until
from nani_pix_bot.jobs.timers.current_image import post_current_image
from nani_pix_bot.jobs.timers.game_timeout import schedule_timeout
from nani_pix_bot.jobs.timers.inactivity import schedule_inactivity_timers
from nani_pix_bot.jobs.timers.turn_timers import cancel_turn_timers
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.game import autostart as autostart_service
from nani_pix_bot.services.settings import stage_config

IDLE_AUTOSTART_JOB_NAME = "idle-autostart"


class AutostartTrigger(enum.Enum):
    OVERTHROW = "overthrow"
    IDLE = "idle"


@dataclass(frozen=True)
class _FirstStagePost:
    """What the group announcement needs, captured while the staging
    game's session was still open — post_current_image runs after that
    session (and its commit) has already closed, same pattern as
    commands/dm_start/preview.py's _FirstStagePost."""

    photo: bytes
    caption: str


@dataclass(frozen=True)
class _AutostartClaim:
    """Everything run_bot_autostart and _build_first_stage_post need
    beyond a live pick, bundled into one parameter so neither function's
    own signature grows past qlty's too-many-parameters threshold.

    `expected_next_starter_id` is turn_state.next_starter_id as the
    caller last observed it before deciding to attempt this autostart —
    see run_bot_autostart's docstring for why that (not "must be None")
    is the actual race-safety invariant."""

    trigger: AutostartTrigger
    dethroned_winner_name: str | None
    expected_next_starter_id: int | None = None


def schedule_idle_autostart(job_queue: JobQueue | None, turn_state: TurnState) -> None:
    if job_queue is None:
        return
    cancel_idle_autostart(job_queue)
    if turn_state.autostart_deadline_at is None:
        return
    delay = seconds_until(turn_state.autostart_deadline_at)
    logger.debug("Scheduling idle-autostart in {:.0f}s", delay)
    job_queue.run_once(idle_autostart_job_callback, when=delay, name=IDLE_AUTOSTART_JOB_NAME)


def cancel_idle_autostart(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    logger.debug("Canceling idle-autostart timer")
    for job in job_queue.get_jobs_by_name(IDLE_AUTOSTART_JOB_NAME):
        job.schedule_removal()


def _autostart_gated(session: Session) -> bool:
    if not settings.get_games_enabled(session):
        return True
    if not settings.get_autostart_enabled(session):
        return True
    return game_service.active_or_setup_game(session) is not None


async def idle_autostart_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires IDLE_AUTOSTART_DELAY after the turn was last opened with no
    game started since. Re-checks everything fresh before attempting a
    claim — a job firing after a human already started a game (or the
    turn was reassigned) must no-op rather than double-post. On a failed
    pick, reschedules AUTOSTART_RETRY_DELAY later rather than going
    silent until the next natural turn-open event."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is not None:
            logger.debug("Idle-autostart fired but the turn is no longer open — no-op")
            return
        if game_service.active_or_setup_game(session) is not None:
            logger.debug("Idle-autostart fired but a game is already running — no-op")
            return
        # _autostart_gated() re-runs this same active_or_setup_game() query
        # as its last check — harmless (a cheap read within the same open
        # session, not a second round-trip worth caching) and kept this way
        # so _autostart_gated() stays a single self-contained gate reused
        # by maybe_overthrow() too, rather than special-cased per caller.
        if _autostart_gated(session):
            logger.debug("Idle-autostart fired but autostart is disabled — no-op")
            return

    claim = _AutostartClaim(trigger=AutostartTrigger.IDLE, dethroned_winner_name=None)
    started = await run_bot_autostart(context, session_factory, claim)
    if started:
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is not None:
            return
        turn_state.autostart_deadline_at = datetime.now(UTC) + game_service.AUTOSTART_RETRY_DELAY
        logger.info(
            "Idle-autostart pick failed — retrying in {}", game_service.AUTOSTART_RETRY_DELAY
        )
        schedule_idle_autostart(context.job_queue, turn_state)


async def maybe_overthrow(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    *,
    winner_id: int | None = None,
    winner_name: str | None = None,
) -> None:
    """Called right after a game concludes — a win (`winner_id` set) or
    an unsolved/timeout ending (`winner_id` None) — from every one of
    this feature's game-ending call sites. Rolls OVERTHROW_PROBABILITY
    once; on a hit, attempts to claim the next game itself, dethroning
    `winner_id` if one was given (they keep their win credit regardless
    — only the next-turn privilege moves). On a miss, a disabled gate,
    or a failed pick, falls through to (re)arming the 24h idle-autostart
    backstop from whatever the normal outcome already committed."""
    logger.debug(
        "Rolling overthrow after a game ended (dethronable winner={})",
        winner_id if winner_id is not None else "none — turn was already open",
    )
    with session_scope(session_factory) as session:
        gated = _autostart_gated(session)

    claimed = False
    if not gated and autostart_service.roll_overthrow():
        claim = _AutostartClaim(
            trigger=AutostartTrigger.OVERTHROW,
            dethroned_winner_name=winner_name,
            expected_next_starter_id=winner_id,
        )
        claimed = await run_bot_autostart(context, session_factory, claim)
    if claimed:
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            schedule_idle_autostart(context.job_queue, turn_state)


def _build_first_stage_post(
    session: Session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    claim: _AutostartClaim,
) -> _FirstStagePost:
    first_stage = game_service.STAGE_ORDER[0]
    first_stage_settings = stage_config.get_stage_config(session, first_stage)
    if game.original_image is None:
        raise RuntimeError("game.original_image is None in _build_first_stage_post")
    pixelated = pixelate_service.pixelate(game.original_image, first_stage_settings.target_width)
    caption_kwargs = {
        "stage": 1,
        "total": len(game_service.STAGE_ORDER),
        "remaining": first_stage_settings.wrong_guess_limit,
        "limit": first_stage_settings.wrong_guess_limit,
    }
    if claim.trigger is AutostartTrigger.IDLE:
        caption = i18n.t("dm_start.game_started_caption_idle", lang, **caption_kwargs)
    elif claim.dethroned_winner_name is not None:
        caption = i18n.t(
            "dm_start.game_started_caption_overthrow_winner",
            lang,
            winner=claim.dethroned_winner_name,
            **caption_kwargs,
        )
    else:
        caption = i18n.t("dm_start.game_started_caption_overthrow_open", lang, **caption_kwargs)
    game_service.activate_game(session, game)
    schedule_timeout(context.job_queue, game)
    schedule_inactivity_timers(context.job_queue, game)
    return _FirstStagePost(photo=pixelated, caption=caption)


async def run_bot_autostart(
    context: ContextTypes.DEFAULT_TYPE, session_factory, claim: _AutostartClaim
) -> bool:
    """The shared "bot claims a game" routine both triggers delegate to
    once a valid pick is in hand. Returns whether a game was actually
    started. Deliberately doesn't reuse commands/dm_start/preview.py's
    private _activate_and_stage_first_post(): that function lives inside
    a DM-flow module jobs/ shouldn't import from, and a bot-started game
    never passes through SETUP/setup-abandon the way a human one does
    (no cancel_setup_abandon call here — none was ever scheduled).

    `claim.expected_next_starter_id` is what the caller already knew
    `turn_state.next_starter_id` to be when it decided to attempt this
    autostart — None for idle_autostart_job_callback (it only fires when
    the turn is open), or the about-to-be-dethroned winner_id for
    maybe_overthrow (None too, for an unsolved/timeout ending that left
    the turn open). It is deliberately *not* "must be None": overthrowing
    a winner who still legitimately holds the turn is this feature's
    whole point, so the real invariant to guard is "nothing changed the
    turn's ownership while gather_pick()'s several real HTTP round-trips
    were in flight" — e.g. a human's /skip @user landing mid-flight and
    designating someone gather_pick() never knew about. activate_game()
    unconditionally clears next_starter_id, so proceeding on a mismatch
    would silently steal a turn nobody agreed to give up."""
    search_client = context.bot_data["search_client"]
    tmdb_client = context.bot_data["tmdb_client"]
    pick = await autostart_service.gather_pick(search_client, tmdb_client)
    if pick is None:
        logger.warning(
            "Bot autostart ({}) found no usable pick after {} attempt(s) — skipping this firing",
            claim.trigger.value,
            autostart_service.AUTOSTART_ATTEMPT_LIMIT,
        )
        return False

    bot_id = context.bot.id
    with session_scope(session_factory) as session:
        if game_service.active_or_setup_game(session) is not None:
            logger.warning(
                "Bot autostart ({}) aborted — a game was started in the meantime",
                claim.trigger.value,
            )
            return False
        turn_state = game_service.get_turn_state(session)
        actual_next_starter_id = turn_state.next_starter_id if turn_state is not None else None
        if actual_next_starter_id != claim.expected_next_starter_id:
            logger.warning(
                "Bot autostart ({}) aborted — turn ownership changed in the meantime "
                "(expected next_starter_id={}, now {})",
                claim.trigger.value,
                claim.expected_next_starter_id,
                actual_next_starter_id,
            )
            return False
        lang = settings.get_language(session)
        players.get_or_create_player(session, bot_id, username=context.bot_data.get("bot_username"))
        game = game_service.create_setup_game(session, starter_id=bot_id)
        game_service.stage_result(game, pick.anime.result, source=pick.anime.source)
        game.original_image = pick.screenshot.image_bytes
        game.screenshot_source = pick.screenshot.provider
        setattr(game, pick.screenshot.provider.id_attr_name, pick.screenshot.provider_id)
        game_service.clear_turn_timers(session)
        first_stage_post = _build_first_stage_post(session, context, game, lang, claim)
        game_service.clear_autostart(session)
        game_id = game.id

    cancel_turn_timers(context.job_queue)
    cancel_idle_autostart(context.job_queue)
    logger.info(
        "Bot autostart ({}) claimed game {} — anime source={}, screenshot provider={}",
        claim.trigger.value,
        game_id,
        pick.anime.source,
        pick.screenshot.provider,
    )
    await post_current_image(
        context, session_factory, photo=first_stage_post.photo, caption=first_stage_post.caption
    )
    return True
