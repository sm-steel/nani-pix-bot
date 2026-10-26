import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import EventType, GameStatus, OutboxKind
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.seasons import bones, consumer

ITEM = "demo_3/name_flair.bone"


def _outbox(session):
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.BONE_FOUND)
    return list(session.scalars(stmt.order_by(AnnouncementOutbox.id)))


def _owned_rows(session, player_id, item_id) -> int:
    stmt = select(func.count()).select_from(OwnedItem)
    stmt = stmt.where(OwnedItem.player_id == player_id, OwnedItem.item_id == item_id)
    return session.scalar(stmt)


@pytest.fixture
def win(session, finished_game):
    def _win(player=1, **fields):
        fields = {"haunt_twist": "kitsune_mirror"} | fields
        game = finished_game(GameStatus.WON, winner_id=player, **fields)
        event = events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=player, subject_id=2, game_id=game.id),
        )
        return game, event

    return _win


def test_haunted_and_boss_wins_count_plain_and_unsolved_do_not(
    session, yokai_season, finished_game
):
    finished_game(GameStatus.WON, winner_id=1, haunt_twist="kitsune_mirror")
    finished_game(GameStatus.WON, winner_id=1, boss_game=True)
    finished_game(GameStatus.WON, winner_id=1)
    finished_game(GameStatus.UNSOLVED, winner_id=1, haunt_twist="kitsune_mirror")
    finished_game(GameStatus.WON, winner_id=2, haunt_twist="kitsune_mirror")
    other = SeasonSchedule(
        run_id="demo_3",
        start_at=yokai_season.start_at,
        end_at=yokai_season.end_at,
        status=yokai_season.status,
        created_by=9,
    )
    session.add(other)
    session.flush()
    finished_game(GameStatus.WON, winner_id=1, haunt_twist="kitsune_mirror", season_id=other.id)
    assert bones.count(session, yokai_season.id, 1) == 2


def test_third_bone_grants_the_item_once(session, win):
    for _ in range(4):
        win(1)
    assert inventory.owns(session, 1, ITEM)
    rows = _outbox(session)
    assert [r.payload["bones"] for r in rows] == [1, 2, 3]
    assert [r.payload["granted"] for r in rows] == [False, False, True]
    assert _owned_rows(session, 1, ITEM) == 1


def test_refinished_haunted_win_is_not_counted_twice(session, win):
    _game, event = win(1)
    consumer.on_event(session, event)  # a refinish re-emits the same win
    assert [r.payload["bones"] for r in _outbox(session)] == [1]
    assert bones.count(session, _game.season_id, 1) == 1


def test_a_plain_win_gives_no_bone(session, win):
    win(1, haunt_twist=None)
    assert _outbox(session) == []


def test_a_boss_win_is_a_bone(session, win):
    win(1, haunt_twist=None, boss_game=True)
    assert [r.payload["bones"] for r in _outbox(session)] == [1]


def test_a_run_without_bone_set_does_nothing(session, active_season, emit_win, tagged_game):
    tagged_game.haunt_twist = "kitsune_mirror"
    tagged_game.status = GameStatus.WON
    tagged_game.winner_id = 1
    session.flush()
    emit_win(winner=1, stage=1)
    assert _outbox(session) == []
