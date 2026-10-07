from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import catalogue, engine, status
from nani_pix_bot.services.achievements.status import State, View

pytestmark = pytest.mark.achievements

ME, OTHER = 1, 2
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _players(session: Session) -> None:
    session.add_all([Player(telegram_user_id=ME), Player(telegram_user_id=OTHER)])
    session.flush()


def _by_key(items: list[status.Status]) -> dict[str, status.Status]:
    return {s.defn.key: s for s in items}


def test_untouched_partial_earned_and_hidden(session: Session) -> None:
    _players(session)
    for game in (1, 2):
        events.emit(
            session, EventType.GUESS, events.Involved(actor_id=ME, game_id=game), correct=False
        )
    engine.grant(session, engine.GrantRequest(ME, "clutch"))

    items = _by_key(status.build(session, ME, NOW))

    assert items["persistent"].state is State.PARTIAL
    assert (items["persistent"].value, items["persistent"].target) == (2, 5)
    assert items["clutch"].state is State.EARNED
    assert items["first_try"].state is State.UNTOUCHED
    assert items["so_close"].state is State.HIDDEN


def test_a_group_unique_held_by_someone_else_is_taken(session: Session) -> None:
    _players(session)
    engine._claim(session, "pioneer", 1, OTHER)

    items = _by_key(status.build(session, ME, NOW))

    assert items["pioneer"].state is State.TAKEN
    assert items["pioneer"].holder_id == OTHER
    assert items["milestone_keeper"].state is State.UNTOUCHED


def test_a_ladder_shows_its_top_tier_and_the_next_target(session: Session) -> None:
    _players(session)
    engine.grant(session, engine.GrantRequest(ME, "sharpshooter", 1))
    engine.grant(session, engine.GrantRequest(ME, "sharpshooter", 2))

    item = _by_key(status.build(session, ME, NOW))["sharpshooter"]

    assert (item.state, item.tier, item.target) == (State.EARNED, 2, 10)


def test_champions_count_wins_and_otherwise_show_the_live_race(session: Session) -> None:
    _players(session)
    engine.grant(session, engine.GrantRequest(ME, "champion_week", 1, "2026-W40"))
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=ME, subject_id=OTHER, game_id=1),
        # every field a GAME_WON-triggered progress function reads (Task 2's payload)
        stage=1,
        hard_mode=False,
        how="guess",
        pot=0,
        seconds=None,
        last_slot=False,
        distinct_guessers=1,
        winner_wrong=0,
        first_guess=False,
        ended_at=NOW.isoformat(),
    )

    items = _by_key(status.build(session, ME, NOW))

    assert (items["champion_week"].state, items["champion_week"].tier) == (State.EARNED, 1)
    assert (items["champion_month"].state, items["champion_month"].rank) == (State.RACE, 1)


def test_views_order_and_filter_sections() -> None:
    clutch, first_try, so_close = (catalogue.get(k) for k in ("clutch", "first_try", "so_close"))
    items = [
        status.Status(first_try, State.UNTOUCHED),
        status.Status(so_close, State.HIDDEN),
        status.Status(catalogue.get("persistent"), State.PARTIAL, value=4, target=5),
        status.Status(catalogue.get("regular"), State.PARTIAL, value=1, target=7),
        status.Status(clutch, State.EARNED, tier=1, granted_at=NOW),
    ]

    all_view = [s.defn.key for s in status.ordered(items, View.ALL)]
    assert all_view == ["clutch", "persistent", "regular", "first_try", "so_close"]
    assert [s.defn.key for s in status.ordered(items, View.EARNED)] == [
        "clutch",
        "persistent",
        "regular",
    ]
    assert [s.defn.key for s in status.ordered(items, View.NOT_YET)] == [
        "persistent",
        "regular",
        "first_try",
        "so_close",
    ]


def test_top_ranks_by_points_then_whoever_got_there_first(session: Session) -> None:
    _players(session)
    engine.grant(session, engine.GrantRequest(OTHER, "clutch"))  # Gold, 8
    engine.grant(session, engine.GrantRequest(ME, "clutch"))  # Gold, 8, later

    rows = status.top(session, limit=10)

    # 200 💠 each stays under Pixel Magnate's 500, so both hold exactly 8 points.
    assert [r.player_id for r in rows] == [OTHER, ME]
    assert status.rank_of(session, ME) == 2
    assert status.points_of(session, ME) == 8
    assert status.ranked_count(session) == 2
