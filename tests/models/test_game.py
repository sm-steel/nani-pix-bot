from sqlalchemy import inspect
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player


def _make_starter(session: Session, telegram_user_id: int = 1) -> Player:
    starter = Player(telegram_user_id=telegram_user_id)
    session.add(starter)
    session.commit()
    return starter


def test_new_game_defaults_to_setup_with_no_wrong_guesses(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, original_image=b"file123")
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.current_stage is None
    assert fetched.wrong_guess_count == 0
    assert fetched.winner_id is None
    assert fetched.created_at is not None
    assert fetched.setup_step == SetupStep.PICKING_METHOD
    assert fetched.setup_deadline is None
    assert fetched.inactivity_nudge_at is None
    assert fetched.inactivity_advance_at is None
    assert fetched.shikimori_id is None
    assert fetched.jikan_id is None
    assert fetched.tmdb_id is None
    assert fetched.screenshot_source is None


def test_game_can_be_created_with_no_image_yet(session: Session) -> None:
    # The screenshot-less /newgame entry point creates a SETUP row before
    # any image exists — original_image must be genuinely optional.
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id)
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.original_image is None


def test_game_stores_provider_ids_independently(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        shikimori_id=52991,
        jikan_id=123,
        tmdb_id=456,
        screenshot_source="tmdb",
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.shikimori_id == 52991
    assert fetched.jikan_id == 123
    assert fetched.tmdb_id == 456
    assert fetched.screenshot_source == "tmdb"


def test_game_original_image_round_trips_binary_data(session: Session) -> None:
    starter = _make_starter(session)
    image_bytes = bytes(range(256))  # exercise every byte value, not just ASCII
    game = Game(starter_id=starter.telegram_user_id, original_image=image_bytes)
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.original_image == image_bytes


def test_game_original_image_is_deferred_loaded(session: Session) -> None:
    # Routine queries (status checks, the /guess hot path) shouldn't pull
    # a multi-hundred-KB blob every time — see models/game.py's docstring
    # on original_image.
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, original_image=b"some bytes")
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert "original_image" in inspect(fetched).unloaded

    # Accessing it still works — deferred just means "not loaded eagerly."
    assert fetched.original_image == b"some bytes"
    assert "original_image" not in inspect(fetched).unloaded


def test_game_stores_synonyms_as_a_json_list(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_image=b"file123",
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren", "Frieren at the Funeral"],
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert fetched.current_stage == PixelStage.STAGE_1


def test_game_winner_references_a_different_player(session: Session) -> None:
    starter = _make_starter(session, telegram_user_id=1)
    winner = _make_starter(session, telegram_user_id=2)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_image=b"file123",
        status=GameStatus.WON,
        winner_id=winner.telegram_user_id,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.winner_id == winner.telegram_user_id
    assert fetched.starter_id == starter.telegram_user_id
