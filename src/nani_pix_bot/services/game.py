"""The game state machine — the only module that mutates a Game row. See
MECHANICS.md for the rules this implements."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services.anilist import AniListResult

TURN_STATE_ID = 1


def get_or_create_player(
    session: Session, telegram_user_id: int, *, username: str | None = None
) -> Player:
    """Look up a player, creating the row if this is their first time. On
    an existing row, opportunistically refreshes `username`."""
    player = session.get(Player, telegram_user_id)
    if player is None:
        player = Player(telegram_user_id=telegram_user_id, username=username)
        session.add(player)
    elif username is not None:
        player.username = username
    return player


def _get_turn_state(session: Session) -> TurnState | None:
    return session.get(TurnState, TURN_STATE_ID)


def _get_or_create_turn_state(session: Session) -> TurnState:
    turn_state = _get_turn_state(session)
    if turn_state is None:
        turn_state = TurnState(id=TURN_STATE_ID)
        session.add(turn_state)
    return turn_state


def active_or_setup_game(session: Session) -> Game | None:
    """The one game currently SETUP or ACTIVE, if any — there's never more
    than one (enforced here, not by a DB constraint; see ARCHITECTURE.md)."""
    stmt = select(Game).where(Game.status.in_([GameStatus.SETUP, GameStatus.ACTIVE]))
    return session.scalars(stmt).first()


def can_start(session: Session, user_id: int) -> bool:
    """Whether `user_id` may DM the bot a screenshot to start a new game
    right now — see MECHANICS.md's "Starting a game"."""
    if active_or_setup_game(session) is not None:
        return False
    turn_state = _get_turn_state(session)
    return turn_state is None or turn_state.next_starter_id in (None, user_id)


def create_setup_game(session: Session, *, starter_id: int, original_file_id: str) -> Game:
    game = Game(starter_id=starter_id, original_file_id=original_file_id, status=GameStatus.SETUP)
    session.add(game)
    session.flush()  # populate game.id for the caller without a full commit
    return game


def activate_game(session: Session, game: Game, result: AniListResult) -> None:
    """Finalize game setup once the starter has picked an AniList result:
    cache its title/synonyms, move to the X10 stage, and open the turn
    (the designated starter's turn is now consumed)."""
    game.anilist_id = result.anilist_id
    game.title_romaji = result.title_romaji
    game.title_english = result.title_english
    game.title_native = result.title_native
    game.synonyms = result.synonyms
    game.status = GameStatus.ACTIVE
    game.current_stage = PixelStage.X10
    game.wrong_guess_count = 0

    turn_state = _get_or_create_turn_state(session)
    turn_state.next_starter_id = None
