"""The /guess command — see MECHANICS.md's "Guess matching" and
"Pixelation stages" sections."""

from dataclasses import dataclass

from loguru import logger
from telegram import Message, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow.stage_post import (
    Announcement,
    PostedLog,
    prepare_stage_advanced_announcement,
    require_original_image,
    send_announcement,
    with_suffix,
)
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.jobs.timers.current_image import RevealTarget, RevealWinner
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.economy import earning, settlement
from nani_pix_bot.services.economy.messages import earnings_suffix
from nani_pix_bot.services.game import guesses

# Outcomes whose post reveals the title anyway.
_NO_PARTIAL_REVEAL = frozenset(
    {
        game_service.GuessOutcome.WON,
        game_service.GuessOutcome.UNSOLVED,
        game_service.GuessOutcome.VOTE_OPENED,
    }
)


def _partial_reveal_line(session, game: Game, lang: str) -> str:
    """The masked-title line a partly-right wrong guess earns (issue #250)."""
    row = guesses.latest(session, game.id)
    if row is None or row.partial_reveal is None:
        return ""
    return "\n" + i18n.t("guess.partial_reveal", lang, reveal=row.partial_reveal)


def _reveal_winner(user: User) -> RevealWinner:
    """The reveal badge's winner: "@username", or the full name for a
    player with no username."""
    return RevealWinner(user.id, f"@{user.username}" if user.username else user.full_name)


def _prepare_won_announcement(
    session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    user: User,
) -> Announcement:
    winner = _reveal_winner(user)
    original_bytes = require_original_image(game, "a WON outcome")
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    caption = i18n.t(
        "guess.won_caption",
        lang,
        winner=user.full_name,
        title=game_service.display_title(game, lang),
    )
    caption += game_service.game_id_line(game.id, lang)
    return Announcement(photo=original_bytes, caption=caption, reveal=RevealTarget(game.id, winner))


def _prepare_hard_mode_won_announcement(
    session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    user: User,
) -> Announcement:
    """The hard-mode analogue of _prepare_won_announcement — reveals the
    stored screenshot pair via post_current_images (a 2-photo album)
    instead of a single post_current_image call. Timer/turn-state
    handling is identical to the normal-mode helper; only the reveal
    photos and caption key differ."""
    winner = _reveal_winner(user)
    photos = game_service.hard_mode_reveal_images(game)
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    caption = i18n.t(
        "guess.hard_mode_won_caption",
        lang,
        winner=user.full_name,
        title=game_service.display_title(game, lang),
    )
    caption += game_service.game_id_line(game.id, lang)
    return Announcement(photos=photos, caption=caption, reveal=RevealTarget(game.id, winner))


def _prepare_turn_advanced_announcement(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> Announcement:
    """Handles the new GuessOutcome.TURN_ADVANCED — the hard-mode
    analogue of prepare_stage_advanced_announcement. By the time this
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
    game_service.reset_inactivity_clock(session, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    caption = i18n.t(
        "guess.hard_mode_turn_advanced_caption",
        lang,
        stage=progress.number,
        total=progress.total,
        remaining=progress.remaining,
        limit=progress.limit,
    )
    return Announcement(
        photos=(pixelated_a, pixelated_b),
        caption=caption,
        is_stage_post=True,
        posted_log=PostedLog(
            stage=f"hard-mode turn {progress.number}/{progress.total}", game_id=game.id
        ),
    )


def _prepare_unsolved_announcement(
    context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> Announcement:
    original_bytes = require_original_image(game, "an UNSOLVED outcome")
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    caption = i18n.t("guess.unsolved_caption", lang, title=game_service.display_title(game, lang))
    caption += game_service.game_id_line(game.id, lang)
    return Announcement(photo=original_bytes, caption=caption, reveal=RevealTarget(game.id, None))


def _prepare_vote_opened(context: ContextTypes.DEFAULT_TYPE, game: Game) -> None:
    """VOTE_OPENED: the round isn't over — stop its game clocks and start
    the vote's own. The ballot is posted after commit (guess_command)."""
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    timeout_module.schedule_vote_close(context.job_queue, game)


def _prepare_won_announcement_dispatch(
    session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    user: User,
) -> Announcement:
    """Picks the hard-mode or normal-mode WON announcement helper based
    on game.hard_mode — hoisted out of guess_command's own dispatch
    purely to keep its cyclomatic complexity down (see CLAUDE.md's
    Tooling section), same reasoning as require_original_image/
    send_announcement. Neither _prepare_won_announcement nor
    _prepare_hard_mode_won_announcement is touched by this wrapper."""
    if game.hard_mode:
        return _prepare_hard_mode_won_announcement(session, context, game, lang, user)
    return _prepare_won_announcement(session, context, game, lang, user)


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
    game_service.reset_inactivity_clock(session, game)
    timeout_module.schedule_inactivity_timers(context.job_queue, game)
    return i18n.t(
        wrong_feedback_key,
        lang,
        remaining=progress.remaining,
        limit=progress.limit,
        stage=progress.number,
        total=progress.total,
    )


def _dispatch_non_won_outcome(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str, outcome
) -> tuple[Announcement | None, bool, str | None]:
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
        return prepare_stage_advanced_announcement(session, context, game, lang), False, None
    if outcome is game_service.GuessOutcome.TURN_ADVANCED:
        return _prepare_turn_advanced_announcement(session, context, game, lang), False, None
    if outcome is game_service.GuessOutcome.VOTE_OPENED:
        _prepare_vote_opened(context, game)
        return None, False, None
    # The only outcome left is GuessOutcome.UNSOLVED (normal mode only: a
    # final hard-mode miss always has a guesser, so it opens a vote instead).
    return _prepare_unsolved_announcement(context, game, lang), True, None


@dataclass(frozen=True)
class _GuessResult:
    """Everything guess_command sends once its session has committed —
    nothing reaches Telegram while the guess (and the currency it paid) could
    still be rolled back by a failed send."""

    game_id: int
    reply_text: str | None
    announcement: Announcement | None
    needs_cleanup_after_send: bool


async def _send_guess_result(
    message: Message, context: ContextTypes.DEFAULT_TYPE, session_factory, result: _GuessResult
) -> None:
    if result.reply_text is not None:
        await message.reply_text(result.reply_text)
    if result.announcement is not None:
        sent = await send_announcement(context, session_factory, result.announcement)
        if result.needs_cleanup_after_send:
            timeout_module.clear_image_if_sent(session_factory, result.game_id, sent)


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
        logger.info("sent an empty /guess — replied with usage")
        await message.reply_text(i18n.t("guess.usage", lang))
        return

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
        outcome = game_service.record_guess(
            session, game, guesser_id=user.id, guess_text=guess_text
        )
        earnings = earning.award_guess(
            session, game, guesser_id=user.id, won=outcome is game_service.GuessOutcome.WON
        )
        suffix = earnings_suffix(session, game, earnings, lang, player_name=user.full_name)
        if outcome not in _NO_PARTIAL_REVEAL:
            suffix = _partial_reveal_line(session, game, lang) + suffix
        if outcome is game_service.GuessOutcome.WON:
            announcement = _prepare_won_announcement_dispatch(session, context, game, lang, user)
            result = _GuessResult(game.id, None, with_suffix(announcement, suffix), True)
        else:
            result = _non_won_result(session, context, game, outcome, suffix)
    # Block closed and committed above — the outcome (and the currency it
    # paid) is durable now regardless of whether the reply/announcement
    # below actually reaches the group (see post_current_image's docstring).
    await _send_guess_result(message, context, session_factory, result)
    await _after_guess(update, context, outcome, result.game_id)


async def _after_guess(
    update: Update, context: ContextTypes.DEFAULT_TYPE, outcome, game_id: int
) -> None:
    """What follows the committed reply: the vote's ballot, or the
    fire-and-forget overthrow roll.

    maybe_overthrow() can run gather_pick()'s several real HTTP round-trips
    (up to AUTOSTART_ATTEMPT_LIMIT attempts, each against 30s-timeout
    clients). app.py never enables concurrent_updates, so PTB processes
    updates one at a time — awaiting it inline would block every other
    DM/group command bot-wide for however long a hanging provider takes.
    `update=update` lets PTB's error handler attribute any exception to
    this update, same as it would for an awaited call. No overthrow while a
    vote is open: the round isn't over."""
    session_factory = context.bot_data["session_factory"]
    user = update.effective_user
    if outcome is game_service.GuessOutcome.VOTE_OPENED:
        await timeout_module.post_vote_ballot(context, session_factory, game_id)
    elif outcome is game_service.GuessOutcome.WON and user is not None:
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


def _non_won_result(
    session, context: ContextTypes.DEFAULT_TYPE, game: Game, outcome, suffix: str
) -> _GuessResult:
    """The reply/announcement for every outcome but WON. A vote opening has
    no announcement yet (the ballot follows the commit), so its reply is the
    earnings alone; an unsolved ending also refunds the pot."""
    lang = settings.get_language(session)
    if outcome is game_service.GuessOutcome.UNSOLVED:
        suffix += settlement.settle_unsolved(session, game, lang)
    announcement, needs_cleanup_after_send, wrong_reply_text = _dispatch_non_won_outcome(
        session, context, game, lang, outcome
    )
    if outcome is game_service.GuessOutcome.VOTE_OPENED:
        return _GuessResult(game.id, suffix.lstrip(chr(10)) or None, None, False)
    return _GuessResult(
        game.id,
        None if wrong_reply_text is None else wrong_reply_text + suffix,
        with_suffix(announcement, suffix),
        needs_cleanup_after_send,
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
    if game is not None and game.status == GameStatus.VOTING:
        logger.warning("guessed while the hard-mode vote is open")
        await message.reply_text(i18n.t("guess.vote_in_progress", lang))
        return None
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("guessed with no ACTIVE game running")
        await message.reply_text(i18n.t("guess.no_game", lang))
        return None
    if not game.hard_mode and game.current_stage is None:
        # Shouldn't happen for a normal-mode game — an ACTIVE one always
        # has this set. A hard-mode ACTIVE game legitimately has
        # current_stage is None for its whole life (it uses
        # hard_mode_turn instead — see models/game.py), so it's exempt
        # from this defensive guard.
        logger.warning("ACTIVE with no current stage — ignored a guess", game_id=game.id)
        return None
    if user.id == game.starter_id:
        logger.warning("starter tried to guess on their own game", game_id=game.id)
        await message.reply_text(i18n.t("guess.starter_cannot_guess", lang))
        return None
    return game
