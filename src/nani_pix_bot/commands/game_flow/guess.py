"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from dataclasses import dataclass

from loguru import logger
from telegram import Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config


@dataclass(frozen=True)
class _Announcement:
    """What a WON/STAGE_ADVANCED/TURN_ADVANCED/UNSOLVED outcome needs to
    post once its session has committed — captured as plain values (not
    the ORM object) since the announcement happens after that session
    closes.

    Exactly one of `photo`/`photos` is populated: a normal-mode outcome
    sets `photo` (sent via post_current_image), a hard-mode outcome sets
    `photos` (sent via post_current_images as a 2-photo album) — see
    guess_command's send-dispatch below, which picks between the two
    functions based on which field is set."""

    caption: str
    photo: bytes | None = None
    photos: tuple[bytes, bytes] | None = None


def _prepare_won_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, winner_name: str
) -> _Announcement:
    original_bytes = _require_original_image(game, "a WON outcome")
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    caption = i18n.t(
        "guess.won_caption", lang, winner=winner_name, title=game_service.display_title(game, lang)
    )
    return _Announcement(photo=original_bytes, caption=caption)


def _prepare_hard_mode_won_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, winner_name: str
) -> _Announcement:
    """The hard-mode analogue of _prepare_won_announcement — reveals the
    stored screenshot pair via post_current_images (a 2-photo album)
    instead of a single post_current_image call. Timer/turn-state
    handling is identical to the normal-mode helper; only the reveal
    photos and caption key differ."""
    photos = game_service.hard_mode_reveal_images(game)
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    caption = i18n.t(
        "guess.hard_mode_won_caption",
        lang,
        winner=winner_name,
        title=game_service.display_title(game, lang),
    )
    return _Announcement(photos=photos, caption=caption)


def _prepare_turn_advanced_announcement(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    """Handles the new GuessOutcome.TURN_ADVANCED — the hard-mode
    analogue of _prepare_stage_advanced_announcement. By the time this
    runs, record_guess (via hard_mode.record_hard_mode_guess, Task 3)
    has already advanced game.hard_mode_turn and reset
    wrong_guess_count, so this only re-pixelates the stored screenshot
    pair at the new turn's width and reschedules the inactivity clock —
    mirroring jobs/timers/inactivity.py's own _hard_mode_turn_advance,
    minus the turn-advance bookkeeping that path does itself (already
    done here by record_guess)."""
    image_a, image_b = game_service.hard_mode_reveal_images(game)
    width = game_service.hard_mode_turn_width(game)
    pixelated_a = pixelate_service.pixelate(image_a, width, game.pixel_algorithm)
    pixelated_b = pixelate_service.pixelate(image_b, width, game.pixel_algorithm)
    progress = game_service.hard_mode_turn_progress(game)
    game_service.reset_inactivity_clock(game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    caption = i18n.t(
        "guess.hard_mode_turn_advanced_caption",
        lang,
        stage=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
    )
    return _Announcement(photos=(pixelated_a, pixelated_b), caption=caption)


def _prepare_hard_mode_unsolved_announcement(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    """The hard-mode analogue of _prepare_unsolved_announcement — reveals
    the stored screenshot pair via post_current_images instead of a
    single post_current_image call. Doesn't need a hard-mode analogue of
    _require_original_image: hard_mode_reveal_images already raises a
    RuntimeError itself if either image is missing."""
    photos = game_service.hard_mode_reveal_images(game)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    caption = i18n.t(
        "guess.hard_mode_unsolved_caption", lang, title=game_service.display_title(game, lang)
    )
    return _Announcement(photos=photos, caption=caption)


def _prepare_stage_advanced_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    # guess_command already checked this is set — restores the type
    # narrowing lost by passing `game` across a function boundary, same
    # as _require_original_image does for original_image below.
    if game.current_stage is None:
        raise RuntimeError("game.current_stage is None on a STAGE_ADVANCED outcome")
    original_bytes = _require_original_image(game, "a STAGE_ADVANCED outcome")
    target_width = stage_config.get_stage_config(session, game.current_stage).target_width
    pixelated = pixelate_service.pixelate(original_bytes, target_width, game.pixel_algorithm)
    progress = game_service.stage_progress(session, game)
    game_service.reset_inactivity_clock(game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    caption = i18n.t(
        "guess.stage_advanced_caption",
        lang,
        stage=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
    )
    return _Announcement(photo=pixelated, caption=caption)


def _prepare_unsolved_announcement(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    original_bytes = _require_original_image(game, "an UNSOLVED outcome")
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    caption = i18n.t("guess.unsolved_caption", lang, title=game_service.display_title(game, lang))
    return _Announcement(photo=original_bytes, caption=caption)


def _require_original_image(game: Game, situation: str) -> bytes:
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


def _prepare_won_announcement_dispatch(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, winner_name: str
) -> _Announcement:
    """Picks the hard-mode or normal-mode WON announcement helper based
    on game.hard_mode — hoisted out of guess_command's own dispatch
    purely to keep its cyclomatic complexity down (see CLAUDE.md's
    Tooling section), same reasoning as _require_original_image/
    _send_announcement. Neither _prepare_won_announcement nor
    _prepare_hard_mode_won_announcement is touched by this wrapper."""
    if game.hard_mode:
        return _prepare_hard_mode_won_announcement(session, context, game, lang, winner_name)
    return _prepare_won_announcement(session, context, game, lang, winner_name)


def _prepare_unsolved_announcement_dispatch(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _Announcement:
    """The UNSOLVED analogue of _prepare_won_announcement_dispatch —
    same hard-mode/normal-mode picking, same reason for existing."""
    if game.hard_mode:
        return _prepare_hard_mode_unsolved_announcement(context, game, lang)
    return _prepare_unsolved_announcement(context, game, lang)


def _prepare_wrong_feedback(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> str:
    """Builds the WRONG-outcome reply text and does the same
    reset-inactivity-clock/reschedule bookkeeping guess_command's WRONG
    branch always did, now hoisted out to keep guess_command's own
    complexity down. Picks hard_mode_turn_progress over stage_progress
    for a hard-mode game — even though, per hard_mode.py's
    record_hard_mode_guess docstring, HARD_MODE_WRONG_GUESS_LIMIT == 1
    means this branch can never actually fire for one in practice (see
    the regression test in test_guess.py); implemented anyway for
    symmetry, not special-cased away."""
    if game.hard_mode:
        progress = game_service.hard_mode_turn_progress(game)
        wrong_feedback_key = "guess.hard_mode_wrong_feedback"
    else:
        progress = game_service.stage_progress(session, game)
        wrong_feedback_key = "guess.wrong_feedback"
    game_service.reset_inactivity_clock(game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    return i18n.t(
        wrong_feedback_key,
        lang,
        remaining=progress.remaining,
        limit=progress.limit,
        stage=progress.number,
        total=progress.total,
    )


async def _send_announcement(
    context: ContextTypes.DEFAULT_TYPE, session_factory, announcement: _Announcement
) -> Message | tuple[Message, ...] | None:
    """Picks post_current_image vs. post_current_images based on which
    of _Announcement's photo/photos fields is populated, and sends it.
    Hoisted out of guess_command purely to keep its own cyclomatic
    complexity down, same reasoning as _require_original_image above."""
    if announcement.photos is not None:
        return await timeout_module.post_current_images(
            context, session_factory, photos=announcement.photos, caption=announcement.caption
        )
    if announcement.photo is not None:
        return await timeout_module.post_current_image(
            context, session_factory, photo=announcement.photo, caption=announcement.caption
        )
    raise RuntimeError("_Announcement has neither photo nor photos set")


def _dispatch_non_won_outcome(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, outcome
) -> tuple[_Announcement | None, bool, str | None]:
    """Routes every recorded GuessOutcome except WON to its announcement/
    reply text (guess_command handles WON itself — see its own
    hard_mode branch — so this doesn't need a winner_name param to stay
    under qlty's "many parameters" threshold). Hoisted out of
    guess_command entirely, not just each hard-mode branch individually,
    to keep guess_command's own cyclomatic complexity under qlty's
    complexity threshold once TURN_ADVANCED's arm and every WRONG/
    UNSOLVED hard-mode branch are accounted for (see CLAUDE.md's Tooling
    section). Returns (announcement, needs_cleanup_after_send,
    wrong_reply_text) — WRONG never has an announcement to hand back,
    only reply text for guess_command to actually await sending (this
    function stays synchronous, message.reply_text is the only async
    call any outcome needs, so it's left to the caller)."""
    if outcome is game_service.GuessOutcome.WRONG:
        return None, False, _prepare_wrong_feedback(session, context, game, lang)
    if outcome is game_service.GuessOutcome.STAGE_ADVANCED:
        return _prepare_stage_advanced_announcement(session, context, game, lang), False, None
    if outcome is game_service.GuessOutcome.TURN_ADVANCED:
        return _prepare_turn_advanced_announcement(context, game, lang), False, None
    # The only outcome left is GuessOutcome.UNSOLVED.
    return _prepare_unsolved_announcement_dispatch(context, game, lang), True, None


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]

    guess_text = " ".join(context.args) if context.args else ""
    if not guess_text:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        await message.reply_text(i18n.t("guess.usage", lang))
        return

    announcement: _Announcement | None = None
    needs_cleanup_after_send = False
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = await _validate_guess(session, message, user, lang)
        if game is None:
            return
        # _validate_guess already checked this is set for a normal-mode
        # game — restores the type narrowing lost by returning `game`
        # across a function boundary. A hard-mode game legitimately has
        # current_stage is None for its whole life (it uses
        # hard_mode_turn instead — see models/game.py), so it's exempt.
        if not game.hard_mode and game.current_stage is None:
            raise RuntimeError("game.current_stage is None despite _validate_guess's check")

        players.get_or_create_player(session, user.id, username=user.username)
        logger.debug("{} guessed {!r} on game {}", user.id, guess_text, game.id)
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )
        game_id = game.id
        if outcome is game_service.GuessOutcome.WON:
            announcement = _prepare_won_announcement_dispatch(
                session, context, game, lang, user.full_name
            )
            needs_cleanup_after_send = True
        else:
            announcement, needs_cleanup_after_send, wrong_reply_text = _dispatch_non_won_outcome(
                session, context, game, lang, outcome
            )
            if wrong_reply_text is not None:
                await message.reply_text(wrong_reply_text)
    # Block closed and committed above — the outcome is durable now
    # regardless of whether the announcement below actually reaches the
    # group (see post_current_image's docstring).
    if announcement is not None:
        sent = await _send_announcement(context, session_factory, announcement)
        if needs_cleanup_after_send:
            timeout_module.clear_image_if_sent(session_factory, game_id, sent)

    # Fire-and-forget: maybe_overthrow() can run gather_pick()'s several
    # real HTTP round-trips (up to AUTOSTART_ATTEMPT_LIMIT attempts, each
    # against 30s-timeout clients). app.py never enables
    # concurrent_updates, so PTB processes updates one at a time —
    # awaiting this inline would block every other DM/group command
    # bot-wide for however long a hanging provider takes. `update=update`
    # lets PTB's error handler attribute any exception to this update,
    # same as it would for an awaited call.
    if outcome is game_service.GuessOutcome.WON:
        context.application.create_task(
            timeout_module.maybe_overthrow(
                context, session_factory, winner_id=user.id, winner_name=user.full_name
            ),
            update=update,
        )
    elif outcome is game_service.GuessOutcome.UNSOLVED:
        context.application.create_task(
            timeout_module.maybe_overthrow(context, session_factory), update=update
        )


async def _validate_guess(session, message, user, lang: str) -> Game | None:
    """The game must be ACTIVE (with current_stage set, which an ACTIVE
    normal-mode game always has — an ACTIVE hard-mode game legitimately
    has current_stage None instead, using hard_mode_turn), and the
    starter may not guess on their own round. Replies and returns None
    on the first failure.

    Deliberately does NOT check original_image here — it's a deferred
    column (see models/game.py), and current_stage being set is already
    the same "this is a properly-activated ACTIVE game" invariant
    without touching it, for a normal-mode game. Loading original_image
    on every /guess (most of which are a WRONG outcome that never reads
    it) would defeat the point of deferring it in the first place."""
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("{} guessed with no ACTIVE game running", user.id)
        await message.reply_text(i18n.t("guess.no_game", lang))
        return None
    if not game.hard_mode and game.current_stage is None:
        # Shouldn't happen for a normal-mode game — an ACTIVE one always
        # has this set. A hard-mode ACTIVE game legitimately has
        # current_stage is None for its whole life (it uses
        # hard_mode_turn instead — see models/game.py), so it's exempt
        # from this defensive guard.
        return None
    if user.id == game.starter_id:
        logger.warning("Starter {} tried to guess on their own game {}", user.id, game.id)
        await message.reply_text(i18n.t("guess.starter_cannot_guess", lang))
        return None
    return game
