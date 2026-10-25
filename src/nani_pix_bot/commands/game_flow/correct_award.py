"""What /correct does once its target is known, whether it was typed as
@username or picked from the menu: the checks on the game and the caller,
the forced win, and its reveal. Nothing here replies; callers turn a refusal
key into a reply or an alert."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.jobs.reveal import RevealWinner
from nani_pix_bot.jobs.timers.current_image import RevealTarget
from nani_pix_bot.models.enums import CosmeticSlot, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.cosmetics import flair, inventory
from nani_pix_bot.services.economy import earning
from nani_pix_bot.services.economy.messages import earnings_suffix


@dataclass(frozen=True)
class Winner:
    player_id: int
    name: str  # "@" + the username as typed, or the picked player's display name


@dataclass(frozen=True)
class Reveal:
    """Everything the reveal needs once the forced win is committed."""

    target: RevealTarget
    image: bytes | None  # a normal game's original image
    photos: tuple[bytes, bytes] | None  # a hard-mode game's screenshot pair
    caption: str
    winner: Winner


def check_game(session: Session, user_id: int) -> Game | str | None:
    """The ACTIVE game `user_id` may /correct in; otherwise the refusal key, or
    None when the refusal is silent. Every check that must pass before /correct
    even looks at a target: an ACTIVE game exists, the caller is its starter,
    and at least one guess has been made."""
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE:
        logger.warning("ran /correct with no ACTIVE game running")
        return "correct.no_game"
    if game.starter_id != user_id:
        logger.warning("non-starter tried /correct", game_id=game.id)
        return "correct.not_starter"
    if game.total_guess_count == 0:
        logger.warning("tried /correct before any guess", game_id=game.id)
        return "correct.no_guesses_yet"
    if not game.hard_mode and game.original_image is None:
        # Shouldn't happen for an ACTIVE game — defensive guard.
        logger.warning("ACTIVE with no original image — ignored /correct", game_id=game.id)
        return None
    return game


def _prepare_reveal(
    game: Game, winner_name: str, lang: str
) -> tuple[bytes | None, tuple[bytes, bytes] | None, str]:
    """Builds the reveal payload (exactly one of the first two return
    values is populated) and caption for the win /correct just forced —
    a hard-mode game reveals its stored screenshot pair via
    post_reveal_pair (the reveal video, or the photo as the fallback, then
    the other screenshot opening the clue album), a normal-mode game reveals
    original_image via post_reveal (the reveal video, or the photo as
    the fallback) — same one-of-two-fields
    dispatch shape as stage_post.py's Announcement/send_announcement.
    check_game already checked original_image is set for a normal-mode
    game, so the RuntimeError here restores the type narrowing lost by
    returning `game` across that function boundary — it should never
    actually fire."""
    title = game_service.display_title(game, lang)
    if game.hard_mode:
        photos = game_service.hard_mode_reveal_images(game)
        caption = i18n.t("correct.hard_mode_caption", lang, winner=winner_name, title=title)
        caption += game_service.game_id_line(game.id, lang)
        return None, photos, caption
    original_bytes = game.original_image
    if original_bytes is None:
        raise RuntimeError("game.original_image is None despite check_game's check")
    caption = i18n.t("correct.caption", lang, winner=winner_name, title=title)
    caption += game_service.game_id_line(game.id, lang)
    return original_bytes, None, caption


def commit_win(
    session: Session, context: ContextTypes.DEFAULT_TYPE, game: Game, winner: Winner, lang: str
) -> Reveal:
    """Force the win and pay it; the reveal is posted after the commit."""
    winner_id = winner.player_id
    target = RevealTarget(
        game.id,
        RevealWinner(
            winner_id,
            players.image_name(session, winner_id, lang, winner.name),
            inventory.look(session, winner_id, CosmeticSlot.WIN_EFFECT),
        ),
    )
    image, photos, caption = _prepare_reveal(
        game, flair.named(session, winner_id, winner.name), lang
    )
    logger.info(
        "starter used /correct to award the win to {target}",
        target=players.describe_player_id(session, winner_id),
        target_id=winner_id,
        game_id=game.id,
    )
    game_service.force_win(session, game, winner_id=winner_id)
    earnings = earning.award_win(session, game, winner_id=winner_id)
    caption += earnings_suffix(
        session, game, earnings, lang, player_name=flair.named(session, winner_id, winner.name)
    )
    timeout_module.cancel_timeout(context.job_queue, game.id)
    timeout_module.cancel_inactivity_timers(context.job_queue, game.id)
    turn_state = game_service.get_turn_state(session)
    if turn_state is not None:
        timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    return Reveal(target, image, photos, caption, winner)


async def post_win(context: ContextTypes.DEFAULT_TYPE, update: Update, reveal: Reveal) -> None:
    """The reveal of a committed /correct win, then the overthrow roll. The win
    is durable already, whether or not the reveal reaches the group (see
    post_current_image's docstring)."""
    session_factory = context.bot_data["session_factory"]
    if reveal.photos is not None:
        sent = await timeout_module.post_reveal_pair(
            context,
            session_factory,
            target=reveal.target,
            photos=reveal.photos,
            caption=reveal.caption,
        )
    elif reveal.image is None:
        raise RuntimeError("neither a photo pair nor original_image to reveal")
    else:
        sent = await timeout_module.post_reveal(
            context,
            session_factory,
            target=reveal.target,
            photo=reveal.image,
            caption=reveal.caption,
        )
    timeout_module.clear_image_if_sent(session_factory, reveal.target.game_id, sent)
    # Fire-and-forget — see guess.py's identical comment: maybe_overthrow()
    # can run several real HTTP round-trips, and awaiting it inline would
    # block every other DM/group command bot-wide (app.py never enables
    # concurrent_updates). update=update lets PTB's error handler attribute
    # any exception to this update, same as an awaited call would.
    context.application.create_task(
        timeout_module.maybe_overthrow(
            context,
            session_factory,
            winner_id=reveal.winner.player_id,
            winner_name=reveal.winner.name.removeprefix("@"),
        ),
        update=update,
    )
