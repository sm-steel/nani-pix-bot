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
    game = Game(starter_id=starter.telegram_user_id, original_file_id="file123")
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


def test_game_stores_synonyms_as_a_json_list(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_file_id="file123",
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren", "Frieren at the Funeral"],
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.X10,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert fetched.current_stage == PixelStage.X10


def test_game_winner_references_a_different_player(session: Session) -> None:
    starter = _make_starter(session, telegram_user_id=1)
    winner = _make_starter(session, telegram_user_id=2)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_file_id="file123",
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
