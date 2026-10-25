"""The /setwinner menu: `/setwinner <game id>` with no @username lists that
game's guessers, whoever guessed last first, for the admin to name the winner.
Callback data carries the game:

- `sw:<admin>:<game_id>:t:<player>`: name that player the winner.
- `sw:<admin>:<game_id>:p:<page>`: a page of the menu.
- `sw:<admin>:<game_id>:x`: cancel.

A pick re-checks the admin's rights and the game's state, and each menu
message acts once (services/used_menus.py)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import setwinner_apply
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.commands.setwinner_apply import Outcome, Winner
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n, players, settings, used_menus
from nani_pix_bot.services.game import guesses

PREFIX = "sw:"
SPEC = PickerSpec(PREFIX, "/setwinner", state_parts=1)


def _view(session: Session, at: Picker, game: Game, bot_id: int, page: int) -> View:
    lang = settings.get_language(session)
    found = player_picker.entries(session, guesses.winner_candidates(session, game, bot_id=bot_id))
    if not found:
        return i18n.t("setwinner.no_one", lang, game_id=game.id), None
    keyboard = player_picker.picker_keyboard(found, page, at.data(), lang)
    return i18n.t("setwinner.pick_player", lang, game_id=game.id), keyboard


def open_view(session: Session, admin_id: int, game_id: int, bot_id: int) -> View:
    """The menu for game `game_id`, or why that game can't be re-finished."""
    game = setwinner_apply.check_game(session, game_id, settings.get_language(session))
    if isinstance(game, str):
        return game, None
    view = _view(session, Picker(PREFIX, admin_id, (str(game_id),)), game, bot_id, 0)
    logger.info(
        "opened the /setwinner picker for game {target_game}",
        target_game=game_id,
        game_id=game_id,
        has_targets=view[1] is not None,
    )
    return view


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        game = session.get(Game, int(tap.picker.state[0]))
        view = (
            None if game is None else _view(session, tap.picker, game, context.bot.id, tap.number)
        )
    if view is None:
        logger.warning("refused a stale /setwinner page", game_id=int(tap.picker.state[0]))
        await player_picker.refuse_stale(query, context)
        return
    await player_picker.turn_page(query, SPEC, tap.number, view)


def _name_winner(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap
) -> Outcome | str | None:
    """Name the tapped player the winner: the outcome, a refusal to show as an
    alert, or None for a stale menu."""
    game_id, winner_id = int(tap.picker.state[0]), tap.number
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        game = setwinner_apply.check(session, game_id, winner_id, context.bot.id, lang)
        if isinstance(game, str):
            return game
        if winner_id not in guesses.winner_candidates(session, game, bot_id=context.bot.id):
            logger.warning("refused a stale /setwinner pick", target_id=winner_id, game_id=game_id)
            return None
        message = query.message
        if message is not None and not used_menus.claim(
            session, message.chat.id, message.message_id
        ):
            logger.warning("refused a second tap on a used /setwinner menu", game_id=game_id)
            return None
        logger.info(
            "picked {target} as game {target_game}'s winner",
            target=players.describe_player_id(session, winner_id),
            target_id=winner_id,
            target_game=game_id,
            game_id=game_id,
        )
        name = players.display_name(session, winner_id, unmasked=True)
        return setwinner_apply.apply(session, game, Winner(winner_id, name), lang)


async def _pick(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, update: Update, tap: Tap
) -> None:
    if not await player_picker.still_admin(query, context, SPEC):
        return
    result = _name_winner(query, context, tap)
    if result is None:
        await player_picker.refuse_stale(query, context)
        return
    if isinstance(result, str):
        await query.answer(result, show_alert=True)
        return
    await setwinner_apply.deliver(update, context, int(tap.picker.state[0]), result)
    await query.answer()
    await player_picker.edit(query, (result.reply, None))


async def setwinner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    if player_picker.number(tap.picker.state[0]) is None or tap.action == "":
        await player_picker.malformed(query, SPEC)
    elif tap.action == "x":
        await player_picker.cancel(query, context, SPEC)
    elif tap.action == "p":
        await _page(query, context, tap)
    else:
        await _pick(query, context, update, tap)
