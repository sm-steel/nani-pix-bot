"""An achievements failure must never cost a player their action: the
evaluation runs in a savepoint and only its own work is rolled back."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.base import Base
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyReason, EventType, GameStatus, PixelStage
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import achievements
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.achievements import catalogue, engine
from nani_pix_bot.services.economy import wallet
from tests.conftest import LogLine

pytestmark = pytest.mark.achievements

STARTER, ALICE, BOB = 1, 2, 3


@pytest.fixture
def db() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed(session: Session) -> Game:
    session.add_all([Player(telegram_user_id=u, currency=500) for u in (STARTER, ALICE, BOB)])
    session.flush()
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Frieren",
    )
    session.add(game)
    session.flush()
    return game


def _granting_then_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine.grant

    def boom(*args: Any, **kwargs: Any) -> AchievementGrant:
        real(*args, **kwargs)  # the work a failure has to roll back
        msg = "achievements broke"
        raise RuntimeError(msg)

    monkeypatch.setattr(engine, "grant", boom)


def _errors(log_records: list[LogLine]) -> list[LogLine]:
    return [r for r in log_records if r.level == "ERROR"]


def test_a_failing_grant_still_commits_the_guess(
    db: Engine, monkeypatch: pytest.MonkeyPatch, log_records: list[LogLine]
) -> None:
    _granting_then_failing(monkeypatch)
    with Session(db) as session:
        game = _seed(session)
        outcome = game_service.record_guess(session, game, guesser_id=ALICE, guess_text="frieren")
        session.commit()

    assert outcome is game_service.GuessOutcome.WON
    with Session(db) as session:
        assert session.scalars(select(GameGuess)).one().player_id == ALICE
        types = {row.event_type for row in session.scalars(select(EventLog))}
        assert {EventType.GUESS, EventType.GAME_WON} <= types
        assert session.scalars(select(AchievementGrant)).all() == []
        assert session.scalars(select(Game)).one().status is GameStatus.WON
    (error,) = [e for e in _errors(log_records) if e.extra.get("event_type") == "game_won"]
    assert "event_id" in error.extra


def test_a_failing_evaluation_still_commits_a_transfer(
    db: Engine, monkeypatch: pytest.MonkeyPatch, log_records: list[LogLine]
) -> None:
    def boom(_session: Session, _event: object) -> list[AchievementGrant]:
        msg = "achievements broke"
        raise RuntimeError(msg)

    monkeypatch.setattr(achievements, "on_event", boom)
    with Session(db) as session:
        _seed(session)
        alice, bob = session.get(Player, ALICE), session.get(Player, BOB)
        assert alice is not None
        assert bob is not None
        entry = wallet.LedgerEntry(CurrencyReason.TIP)
        wallet.transfer(session, wallet.Party.of(alice), wallet.Party.of(bob), 40, entry)
        session.commit()

    with Session(db) as session:
        assert session.scalars(select(CurrencyTransfer)).one().amount == 40
        bob = session.get(Player, BOB)
        assert bob is not None
        assert bob.currency == 540
    (error,) = _errors(log_records)
    assert error.extra["event_type"] == "currency_moved"


def test_a_failure_inside_a_reward_cascade_keeps_the_outer_grant(
    db: Engine, monkeypatch: pytest.MonkeyPatch, log_records: list[LogLine]
) -> None:
    real = catalogue.triggered_by

    def money_breaks(event_type: EventType) -> tuple[Any, ...]:
        if event_type is EventType.CURRENCY_MOVED:
            msg = "money evaluation broke"
            raise RuntimeError(msg)
        return real(event_type)

    monkeypatch.setattr(catalogue, "triggered_by", money_breaks)
    with Session(db) as session:
        game = _seed(session)
        game_service.record_guess(session, game, guesser_id=ALICE, guess_text="frieren")
        session.commit()

    with Session(db) as session:
        keys = {g.key for g in session.scalars(select(AchievementGrant))}
        assert {"sharpshooter", "pioneer"} <= keys
        rewards = session.scalars(
            select(CurrencyTransfer).where(CurrencyTransfer.reason == CurrencyReason.ACHIEVEMENT)
        ).all()
        assert rewards
    assert _errors(log_records)
    assert {e.extra["event_type"] for e in _errors(log_records)} == {"currency_moved"}
