from datetime import timedelta

from sqlalchemy import select

from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    ItemSource,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonPass, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.seasons import lifecycle, season_pass
from tests.seasons_fixtures import NOW, STARTER, WINNER


def _run() -> SeasonRun:
    run = registry.get("demo_1")
    assert run is not None
    return run


def _events(session, event_type) -> list[EventLog]:
    return list(
        session.scalars(
            select(EventLog).where(EventLog.event_type == event_type).order_by(EventLog.id)
        )
    )


def test_granting_an_item_emits_item_gained(session, active_season, tagged_game) -> None:
    inventory.grant(
        session, WINNER, ItemGrant("demo_1/name_flair.dango", ItemSource.SHOP, active_season.id)
    )
    [row] = _events(session, EventType.ITEM_GAINED)
    assert row.actor_id == WINNER
    assert row.data == {
        "item_id": "demo_1/name_flair.dango",
        "source": "shop",
        "season_id": active_season.id,
    }


def test_an_item_already_owned_emits_nothing(session, active_season, tagged_game) -> None:
    grant = ItemGrant("demo_1/name_flair.dango", ItemSource.SHOP, active_season.id)
    inventory.grant(session, WINNER, grant)
    inventory.grant(session, WINNER, grant)
    assert len(_events(session, EventType.ITEM_GAINED)) == 1


def _give_xp(session, season, player_id, amount) -> None:
    session.add(SeasonXp(season_id=season.id, player_id=player_id, amount=amount, source="win"))
    session.flush()


def test_completing_the_free_track_sets_the_time_and_emits(
    session, active_season, tagged_game
) -> None:
    run = _run()
    _give_xp(session, active_season, WINNER, 120)
    season_pass.sync(session, active_season, run, WINNER)
    progress = session.get(SeasonPass, (active_season.id, WINNER))
    assert progress.free_completed_at is not None
    [row] = _events(session, EventType.TRACK_COMPLETED)
    assert row.data == {"season_id": active_season.id, "premium": False, "first": True}


def test_only_the_first_free_completion_is_first(session, active_season, tagged_game) -> None:
    run = _run()
    for player in (WINNER, STARTER):
        _give_xp(session, active_season, player, 120)
        season_pass.sync(session, active_season, run, player)
    firsts = [row.data["first"] for row in _events(session, EventType.TRACK_COMPLETED)]
    assert firsts == [True, False]


def test_a_second_sync_emits_no_second_completion(session, active_season, tagged_game) -> None:
    run = _run()
    _give_xp(session, active_season, WINNER, 120)
    season_pass.sync(session, active_season, run, WINNER)
    season_pass.sync(session, active_season, run, WINNER)
    assert len(_events(session, EventType.TRACK_COMPLETED)) == 1


def test_completing_the_premium_track_emits_premium(session, active_season, tagged_game) -> None:
    run = _run()
    player = session.get(Player, WINNER)
    wallet.credit(session, player, 500, LedgerEntry(CurrencyReason.GRANT))
    _give_xp(session, active_season, WINNER, 120)
    season_pass.buy_premium(session, active_season, run, player)
    premium = [r.data for r in _events(session, EventType.TRACK_COMPLETED) if r.data["premium"]]
    assert premium == [{"season_id": active_season.id, "premium": True, "first": False}]


def test_finalize_emits_season_ended_per_ranked_player(session, active_season, tagged_game) -> None:
    _give_xp(session, active_season, WINNER, 50)
    _give_xp(session, active_season, STARTER, 20)
    tagged_game.status = GameStatus.WON  # no running games, so the season can finalize
    active_season.status = SeasonStatus.CLOSING
    active_season.end_at = NOW - timedelta(hours=1)
    session.flush()
    lifecycle.advance(session, NOW)
    rows = _events(session, EventType.SEASON_ENDED)
    assert [(r.actor_id, r.data["rank"], r.data["xp"]) for r in rows] == [
        (WINNER, 1, 50),
        (STARTER, 2, 20),
    ]
    assert all(
        r.data["run_id"] == "demo_1" and r.data["season_id"] == active_season.id for r in rows
    )
