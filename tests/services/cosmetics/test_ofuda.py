from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import ConsumableKind, EventType, GameStatus, PixelStage
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.halloween import ArmedOfuda
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import consumables, ofuda
from nani_pix_bot.services.cosmetics.ofuda import ArmRefusal, ArmRefusedError

PLAYER = 1
STARTER = 2
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _new_game(session: Session, **fields) -> Game:
    values = {
        "starter_id": STARTER,
        "status": GameStatus.ACTIVE,
        "current_stage": PixelStage.STAGE_1,
    }
    values.update(fields)
    game = Game(**values)
    session.add(game)
    session.flush()
    return game


def _events(session: Session, event_type: EventType) -> list[tuple[int | None, int | None]]:
    stmt = select(EventLog).where(EventLog.event_type == event_type).order_by(EventLog.id)
    return [(row.actor_id, row.game_id) for row in session.scalars(stmt)]


def _armed_rows(session: Session) -> int:
    return len(list(session.scalars(select(ArmedOfuda))))


@pytest.fixture(autouse=True)
def players(session: Session) -> None:
    session.add_all([Player(telegram_user_id=PLAYER), Player(telegram_user_id=STARTER)])
    session.flush()


@pytest.fixture
def game(session: Session) -> Game:
    return _new_game(session)


@pytest.fixture
def next_game(session: Session) -> Game:
    return _new_game(session)


@pytest.fixture
def ofuda_holder(session: Session) -> None:
    consumables.add(session, PLAYER, ConsumableKind.OFUDA, why="test")


def test_arm_creates_one_row_and_takes_nothing(session, ofuda_holder, game) -> None:
    ofuda.arm(session, game, PLAYER, NOW)
    assert ofuda.is_armed(session, game.id, PLAYER)
    assert consumables.count(session, PLAYER, ConsumableKind.OFUDA) == 1
    assert _armed_rows(session) == 1


def test_arm_logs_the_count_held(session, ofuda_holder, game, log_records) -> None:
    ofuda.arm(session, game, PLAYER, NOW)
    line = next(r for r in log_records if "armed an Ofuda" in r.message)
    assert line.level == "INFO"
    assert line.extra["held"] == 1


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("none_held", ArmRefusal.NONE_HELD),
        ("starter", ArmRefusal.OWN_GAME),
        ("hard_mode", ArmRefusal.HARD_MODE),
        ("won", ArmRefusal.NO_GAME),
        ("no_game", ArmRefusal.NO_GAME),
        ("armed", ArmRefusal.ALREADY_ARMED),
    ],
)
def test_arm_refusals(session, log_records, setup, expected) -> None:
    player = PLAYER
    if setup != "none_held":
        consumables.add(session, PLAYER, ConsumableKind.OFUDA, why="test")
    fields = {
        "starter": {"starter_id": PLAYER},
        "hard_mode": {"hard_mode": True},
        "won": {"status": GameStatus.WON},
    }.get(setup, {})
    game = None if setup == "no_game" else _new_game(session, **fields)
    if setup == "armed":
        assert game is not None
        ofuda.arm(session, game, player, NOW)
    before = _armed_rows(session)

    with pytest.raises(ArmRefusedError) as raised:
        ofuda.arm(session, game, player, NOW)

    assert raised.value.refusal == expected
    assert ofuda.refusal(session, game, player) == expected
    assert _armed_rows(session) == before
    warning = next(r for r in log_records if r.level == "WARNING")
    assert expected.value in warning.message


def test_refusal_is_none_when_armable(session, ofuda_holder, game) -> None:
    assert ofuda.refusal(session, game, PLAYER) is None


def test_forgive_consumes_the_armed_ofuda(session, ofuda_holder, game, log_records) -> None:
    ofuda.arm(session, game, PLAYER, NOW)
    assert ofuda.forgive(session, game, PLAYER) is True
    assert not ofuda.is_armed(session, game.id, PLAYER)
    assert consumables.count(session, PLAYER, ConsumableKind.OFUDA) == 0
    assert _events(session, EventType.OFUDA_USED) == [(PLAYER, game.id)]
    assert any(
        r.level == "INFO"
        and "forgave the miss" in r.message
        and r.extra["left"] == 0
        and r.extra["armed_at"] == NOW.replace(tzinfo=None)
        for r in log_records
    )


def test_forgive_without_an_armed_ofuda_is_false(session, ofuda_holder, game) -> None:
    assert ofuda.forgive(session, game, PLAYER) is False
    assert consumables.count(session, PLAYER, ConsumableKind.OFUDA) == 1
    assert _events(session, EventType.OFUDA_USED) == []


def test_forgive_with_an_empty_count_is_false_and_disarms(session, game, log_records) -> None:
    session.add(ArmedOfuda(player_id=PLAYER, game_id=game.id, armed_at=NOW))
    session.flush()
    assert ofuda.forgive(session, game, PLAYER) is False
    assert not ofuda.is_armed(session, game.id, PLAYER)
    assert _events(session, EventType.OFUDA_USED) == []
    assert any(r.level == "WARNING" for r in log_records)


def test_an_unused_armed_ofuda_stays_held_and_does_not_carry_over(
    session, ofuda_holder, game, next_game
) -> None:
    ofuda.arm(session, game, PLAYER, NOW)
    game.status = GameStatus.WON
    assert consumables.count(session, PLAYER, ConsumableKind.OFUDA) == 1
    assert not ofuda.is_armed(session, next_game.id, PLAYER)
    assert ofuda.forgive(session, next_game, PLAYER) is False
