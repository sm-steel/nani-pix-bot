from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import HexOutcome, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.halloween import ArmedOfuda, Hex, OmikujiDraw
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _setup(session: Session) -> tuple[SeasonSchedule, Game]:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    season = SeasonSchedule(
        run_id="halloween_1",
        start_at=NOW,
        end_at=NOW,
        status=SeasonStatus.ACTIVE,
        created_by=1,
    )
    game = Game(starter_id=1)
    session.add_all([season, game])
    session.commit()
    return season, game


def test_new_game_columns_default_off(session: Session) -> None:
    _, game = _setup(session)

    assert game.boss_game is False
    assert game.event_day is False
    assert game.haunt_twist is None


def test_player_hex_opt_out_defaults_off(session: Session) -> None:
    _setup(session)

    player = session.get(Player, 1)

    assert player is not None
    assert player.hex_opt_out is False


def test_hex_row_round_trips(session: Session) -> None:
    season, game = _setup(session)
    session.add(
        Hex(
            season_id=season.id,
            kind="kappa",
            caster_id=1,
            target_id=2,
            cast_at=NOW,
            resolved_at=NOW,
            outcome=HexOutcome.TRIGGERED,
            game_id=game.id,
        )
    )
    session.commit()

    row = session.query(Hex).one()

    assert (row.kind, row.caster_id, row.target_id, row.game_id) == ("kappa", 1, 2, game.id)
    assert row.expires_at is None
    assert row.outcome == "triggered"


def test_second_omikuji_draw_same_day_is_rejected(session: Session) -> None:
    season, _ = _setup(session)
    day = date(2026, 10, 24)
    session.add(
        OmikujiDraw(season_id=season.id, player_id=1, local_date=day, outcome="kichi", drawn_at=NOW)
    )
    session.commit()
    session.add(
        OmikujiDraw(
            season_id=season.id, player_id=1, local_date=day, outcome="daikichi", drawn_at=NOW
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_armed_ofuda_is_one_per_player_per_game(session: Session) -> None:
    _, game = _setup(session)
    session.add(ArmedOfuda(player_id=1, game_id=game.id, armed_at=datetime.now(UTC)))
    session.commit()
    session.add(ArmedOfuda(player_id=1, game_id=game.id, armed_at=datetime.now(UTC)))

    with pytest.raises(IntegrityError):
        session.commit()
