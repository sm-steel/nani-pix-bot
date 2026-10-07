"""Bot-initiated game starting: the 24h idle-autostart timer, and the
probabilistic "overthrow" trigger fired right after a game concludes —
see the design behind issue #159. Builds on
services/game/autostart.py's pure picking logic; this module owns the
DB write, JobQueue scheduling, and Telegram posting around it."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope, seconds_until
from nani_pix_bot.jobs.timers.current_image import post_stage_images
from nani_pix_bot.jobs.timers.game_timeout import schedule_timeout
from nani_pix_bot.jobs.timers.inactivity import schedule_inactivity_timers
from nani_pix_bot.jobs.timers.quiet import quiet_hours_deferred
from nani_pix_bot.jobs.timers.retry import retry_on_failure
from nani_pix_bot.jobs.timers.turn_timers import cancel_turn_timers
from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import events, i18n, players, quiet_hours, settings
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.game import autostart as autostart_service

IDLE_AUTOSTART_JOB_NAME = "idle-autostart"


class AutostartTrigger(enum.Enum):
    OVERTHROW = "overthrow"
    IDLE = "idle"


@dataclass(frozen=True)
class _FirstTurnPost:
    """What the group announcement needs, captured while the staging
    game's session was still open — post_current_images runs after that
    session (and its commit) has already closed, same pattern as
    commands/dm_start/preview.py's _FirstStagePost. Every autostart game
    is hard mode now, so this carries the screenshot pair's two
    pixelated photos rather than one."""

    photo_a: bytes
    photo_b: bytes
    caption: str


@dataclass(frozen=True)
class _AutostartClaim:
    """Everything run_bot_autostart and _build_first_turn_post need
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
    logger.debug("scheduling idle-autostart in {delay:.0f}s", delay=delay)
    job_queue.run_once(idle_autostart_job_callback, when=delay, name=IDLE_AUTOSTART_JOB_NAME)


def cancel_idle_autostart(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    logger.debug("canceling idle-autostart timer")
    for job in job_queue.get_jobs_by_name(IDLE_AUTOSTART_JOB_NAME):
        job.schedule_removal()


def _autostart_gate_reason(session: Session) -> str | None:
    """Why the bot must not start a game itself right now, or None if
    nothing stops it — the reason goes into the caller's log line."""
    if not settings.get_games_enabled(session):
        return "new games are disabled"
    if not settings.get_autostart_enabled(session):
        return "autostart is disabled"
    if game_service.active_or_setup_game(session) is not None:
        return "a game is already running"
    return None


@job_log_scope()
@retry_on_failure
@quiet_hours_deferred
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
            logger.info("idle-autostart fired but the turn is no longer open — not starting")
            return
        gate_reason = _autostart_gate_reason(session)
        if gate_reason is not None:
            logger.info("idle-autostart fired but {reason} — not starting", reason=gate_reason)
            return

    claim = _AutostartClaim(trigger=AutostartTrigger.IDLE, dethroned_winner_name=None)
    started = await run_bot_autostart(context, session_factory, claim)
    if started:
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is None or turn_state.next_starter_id is not None:
            return
        turn_state.autostart_deadline_at = game_service.deadline_after(
            session, game_service.AUTOSTART_RETRY_DELAY
        )
        logger.info(
            "idle-autostart pick failed — retrying in {delay}",
            delay=game_service.AUTOSTART_RETRY_DELAY,
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
    claimed = False
    if _roll_overthrow(session_factory, winner_id):
        claim = _AutostartClaim(
            trigger=AutostartTrigger.OVERTHROW,
            dethroned_winner_name=winner_name,
            expected_next_starter_id=winner_id,
        )
        claimed = await run_bot_autostart(context, session_factory, claim)
    if claimed:
        if winner_id is not None:
            _record_overthrow(session_factory, winner_id)
        return

    with session_scope(session_factory) as session:
        turn_state = game_service.get_turn_state(session)
        if turn_state is not None:
            schedule_idle_autostart(context.job_queue, turn_state)


def _record_overthrow(session_factory, winner_id: int) -> None:
    """The dethroned winner's `overthrown` event, in its own transaction."""
    try:
        with session_scope(session_factory) as session:
            events.emit(session, EventType.OVERTHROWN, events.Involved(actor_id=winner_id))
    except SQLAlchemyError as error:
        # The game is already claimed; a lost event must not abort the flow.
        logger.error(
            "couldn't record the overthrow of {player}: {error}",
            player=winner_id,
            error=error,
        )


def _describe_holder(session: Session, next_starter_id: int | None) -> str:
    """Who holds the turn, for a log line — `nobody (turn open)` for None."""
    if next_starter_id is None:
        return "nobody (turn open)"
    return players.describe_player_id(session, next_starter_id)


def _roll_overthrow(session_factory, winner_id: int | None) -> bool:
    """maybe_overthrow's gate checks plus the roll itself, logging the
    outcome either way — a skipped or missed roll is as much a part of
    the game's story at INFO as a hit."""
    with session_scope(session_factory) as session:
        gate_reason = _autostart_gate_reason(session)
        if gate_reason is None and quiet_hours.is_quiet(
            settings.get_quiet_hours(session), datetime.now(UTC)
        ):
            gate_reason = "quiet hours"
        holder = _describe_holder(session, winner_id)
    if gate_reason is not None:
        logger.info("overthrow roll skipped after a game ended — {reason}", reason=gate_reason)
        return False
    hit = autostart_service.roll_overthrow()
    logger.info(
        "overthrow roll after a game ended: {outcome} ({chance:.0%} chance; turn held by {holder})",
        outcome="HIT — the bot will try to start the next game" if hit else "miss",
        chance=autostart_service.OVERTHROW_PROBABILITY,
        holder=holder,
    )
    return hit


def _build_first_turn_post(
    session: Session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    claim: _AutostartClaim,
) -> _FirstTurnPost:
    """Every autostart game is hard mode now — activate_game() must run
    first (it's what sets game.hard_mode_turn = 1), since
    hard_mode_turn_width()/hard_mode_turn_progress() below both read
    that field. Contrast the old stage-based version of this function,
    where the equivalent pixelation-width lookup (stage_config, keyed by
    STAGE_ORDER[0]) didn't depend on activate_game() having run yet."""
    game_service.activate_game(session, game)
    if game.hard_mode_image_a is None or game.hard_mode_image_b is None:
        raise RuntimeError("game.hard_mode_image_a/_b is None in _build_first_turn_post")
    width = game_service.hard_mode_turn_width(game)
    pixelated_a = pixelate_service.pixelate(game.hard_mode_image_a, width, game.pixel_algorithm)
    pixelated_b = pixelate_service.pixelate(game.hard_mode_image_b, width, game.pixel_algorithm)
    progress = game_service.hard_mode_turn_progress(game)
    caption_kwargs = {
        "turn": progress.number,
        "total": progress.total,
        "remaining": progress.remaining,
        "limit": progress.limit,
    }
    if claim.trigger is AutostartTrigger.IDLE:
        caption = i18n.t("dm_start.hard_mode_game_started_caption_idle", lang, **caption_kwargs)
    elif claim.dethroned_winner_name is not None:
        caption = i18n.t(
            "dm_start.hard_mode_game_started_caption_overthrow_winner",
            lang,
            winner=claim.dethroned_winner_name,
            **caption_kwargs,
        )
    else:
        caption = i18n.t(
            "dm_start.hard_mode_game_started_caption_overthrow_open", lang, **caption_kwargs
        )
    if game.hard_mode_clue_discount:
        caption += "\n" + i18n.t(
            "hard_mode.discount_line", lang, percent=game.hard_mode_clue_discount
        )
    caption += game_service.game_id_line(game.id, lang)
    schedule_timeout(context.job_queue, game)
    schedule_inactivity_timers(context.job_queue, game)
    return _FirstTurnPost(photo_a=pixelated_a, photo_b=pixelated_b, caption=caption)


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
    tenrai_client = context.bot_data["tenrai_client"]
    pick = await autostart_service.gather_pick(search_client, tmdb_client, tenrai_client)
    if pick is None:
        logger.warning(
            "bot autostart ({trigger}) found no usable pick after {attempts} attempt(s) — "
            "skipping this firing",
            trigger=claim.trigger.value,
            attempts=autostart_service.AUTOSTART_ATTEMPT_LIMIT,
        )
        return False

    bot_id = context.bot.id
    with session_scope(session_factory) as session:
        if game_service.active_or_setup_game(session) is not None:
            logger.warning(
                "bot autostart ({trigger}) aborted — a game was started in the meantime",
                trigger=claim.trigger.value,
            )
            return False
        turn_state = game_service.get_turn_state(session)
        actual_next_starter_id = turn_state.next_starter_id if turn_state is not None else None
        if actual_next_starter_id != claim.expected_next_starter_id:
            logger.warning(
                "bot autostart ({trigger}) aborted — turn ownership changed in the meantime "
                "(expected the turn held by {expected_holder}, now {holder})",
                trigger=claim.trigger.value,
                expected_holder=_describe_holder(session, claim.expected_next_starter_id),
                holder=_describe_holder(session, actual_next_starter_id),
            )
            return False
        lang = settings.get_language(session)
        players.get_or_create_player(
            session, bot_id, username=context.bot_data.get("bot_username"), grant=False
        )
        game = game_service.create_setup_game(
            session, starter_id=bot_id, entry=f"bot autostart ({claim.trigger.value})"
        )
        game_service.stage_result(game, pick.anime.result, source=pick.anime.source)
        # Every autostart pick is hard mode now — no `if` needed.
        # original_image stays None; the screenshot pair lives in
        # hard_mode_image_a/_b instead.
        game.hard_mode = True
        game.hard_mode_clue_discount = game_service.next_clue_discount(session)
        logger.info(
            "HARD MODE clue discount frozen at {percent}%",
            percent=game.hard_mode_clue_discount,
            game_id=game.id,
        )
        game.hard_mode_image_a = pick.screenshot.image_bytes_a
        game.hard_mode_image_b = pick.screenshot.image_bytes_b
        game.shown_screenshot_urls = [pick.screenshot.url_a, pick.screenshot.url_b]
        game.screenshot_source = pick.screenshot.provider
        setattr(game, pick.screenshot.provider.id_attr_name, pick.screenshot.provider_id)
        game_service.clear_turn_timers(session)
        first_turn_post = _build_first_turn_post(session, context, game, lang, claim)
        game_service.clear_autostart(session)
        game_id = game.id

    cancel_turn_timers(context.job_queue)
    cancel_idle_autostart(context.job_queue)
    logger.info(
        "claimed by bot autostart ({trigger}) — anime source={source}, "
        "screenshot provider={provider}",
        trigger=claim.trigger.value,
        source=pick.anime.source,
        provider=pick.screenshot.provider,
        game_id=game_id,
    )
    await post_stage_images(
        context,
        session_factory,
        photos=(first_turn_post.photo_a, first_turn_post.photo_b),
        caption=first_turn_post.caption,
    )
    return True
