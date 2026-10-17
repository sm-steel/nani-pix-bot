"""The author's clue screenshot list (issue #450): a game's clue screenshot
mode and the screenshots its starter picked or uploaded for the shop."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from nani_pix_bot.models import ClueAuthorScreenshot, Player
from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import ClueScreenshotMode, Provider
from nani_pix_bot.models.game import CLUE_SCREENSHOT_MODE_LENGTH, Game


@pytest.fixture
def fk_session() -> Iterator[Session]:
    """SQLite enforces ON DELETE CASCADE only with foreign keys switched on,
    as MariaDB always has them."""
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda conn, _record: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _game(session: Session) -> Game:
    session.add(Player(telegram_user_id=1))
    session.flush()
    game = Game(starter_id=1)
    session.add(game)
    session.flush()
    return game


def test_new_game_defaults_random(session: Session) -> None:
    game = _game(session)
    session.commit()
    session.expire_all()

    stored = session.get(Game, game.id)

    assert stored is not None
    assert stored.clue_screenshot_mode == ClueScreenshotMode.RANDOM


def test_mode_values_fit_the_column() -> None:
    assert max(len(mode.value) for mode in ClueScreenshotMode) <= CLUE_SCREENSHOT_MODE_LENGTH


def test_entry_round_trips(session: Session) -> None:
    game = _game(session)
    session.add(
        ClueAuthorScreenshot(
            game_id=game.id,
            source=Provider.SHIKIMORI,
            url="https://x/1.jpg",
            image=b"png",
            fingerprint=7,
        )
    )
    session.commit()
    session.expire_all()

    row = session.scalars(select(ClueAuthorScreenshot)).one()

    assert (row.source, row.url, row.image, row.fingerprint) == (
        Provider.SHIKIMORI,
        "https://x/1.jpg",
        b"png",
        7,
    )
    assert row.created_at is not None


def test_entries_cascade_on_game_delete(fk_session: Session) -> None:
    game = _game(fk_session)
    fk_session.add_all(
        [
            ClueAuthorScreenshot(game_id=game.id, image=b"a", fingerprint=1),
            ClueAuthorScreenshot(game_id=game.id, image=b"b", fingerprint=2),
        ]
    )
    fk_session.commit()

    fk_session.delete(game)
    fk_session.commit()

    assert fk_session.scalars(select(ClueAuthorScreenshot)).all() == []
