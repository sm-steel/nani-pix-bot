"""The /correct player picker: this round's guessers, whoever guessed last
first, for the starter to award the win to. Callback data carries the game:

- `cor:<starter>:<game_id>:p:<page>`: a page of the picker.
- `cor:<starter>:<game_id>:t:<player>`: award that player the win.
- `cor:<starter>:<game_id>:x`: cancel.

A tap re-runs every /correct check, and a picker left over from another game,
or naming someone who is no longer a candidate, is refused as stale."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow.correct_award import (
    Reveal,
    Winner,
    check_game,
    commit_win,
    post_win,
)
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.game import guesses

PREFIX = "cor:"
SPEC = PickerSpec(PREFIX, "/correct", state_parts=1)


def _candidates(session: Session, game: Game, bot_id: int) -> list[int]:
    return guesses.winner_candidates(session, game, bot_id=bot_id)


def players_view(session: Session, game: Game, bot_id: int, page: int) -> View:
    lang = settings.get_language(session)
    found = player_picker.entries(session, _candidates(session, game, bot_id))
    if not found:
        return i18n.t("correct.no_one", lang), None
    at = Picker(PREFIX, game.starter_id, (str(game.id),))
    return i18n.t("correct.pick_player", lang), player_picker.picker_keyboard(
        found, page, at.data(), lang
    )


def _game_for(session: Session, tap: Tap) -> Game | str | None:
    """The game the tap may act on, a refusal key, or None (silent)."""
    game = check_game(session, tap.picker.opener_id)
    if isinstance(game, Game) and str(game.id) != tap.picker.state[0]:
        logger.warning("refused a stale /correct picker", picker_game_id=tap.picker.state[0])
        return "player_picker.stale"
    return game


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        game = _game_for(session, tap)
        view = (
            players_view(session, game, context.bot.id, tap.number)
            if isinstance(game, Game)
            else None
        )
    if view is None:
        await _refuse(query, context, game if isinstance(game, str) else None)
        return
    await player_picker.turn_page(query, SPEC, tap.number, view)


def _award(context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> Reveal | str | None:
    """Commit the win for the tapped player, or say why not."""
    with session_scope(context.bot_data["session_factory"]) as session:
        game = _game_for(session, tap)
        if not isinstance(game, Game):
            return game
        if tap.number not in _candidates(session, game, context.bot.id):
            logger.warning("refused a stale /correct pick", target_id=tap.number, game_id=game.id)
            return "player_picker.stale"
        name = players.display_name(session, tap.number, unmasked=True)
        logger.info(
            "picked {target} for /correct",
            target=players.describe_player_id(session, tap.number),
            target_id=tap.number,
        )
        return commit_win(
            session, context, game, Winner(tap.number, name), settings.get_language(session)
        )


async def _remove_picker(query: CallbackQuery) -> None:
    """The reveal speaks for itself; the picker goes away."""
    try:
        await query.delete_message()
    except TelegramError as error:
        logger.warning("couldn't remove the /correct picker: {error}", error=str(error))


async def _refuse(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, key: str | None
) -> None:
    if key is None:
        await query.answer()
    else:
        await query.answer(i18n.t(key, player_picker.language(context)), show_alert=True)


async def _pick(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, update: Update, tap: Tap
) -> None:
    result = _award(context, tap)
    if not isinstance(result, Reveal):
        await _refuse(query, context, result)
        return
    await query.answer()
    await _remove_picker(query)
    await post_win(context, update, result)


async def correct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    if tap.action == "x":
        await player_picker.cancel(query, context, SPEC)
    elif tap.action == "t":
        await _pick(query, context, update, tap)
    elif tap.action == "p":
        await _page(query, context, tap)
    else:
        await player_picker.malformed(query, SPEC)
