import pytest
from sqlalchemy import select

from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import (
    ConsumableKind,
    ItemSource,
    OutboxKind,
    SeasonStatus,
    XpSource,
)
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Reward
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cosmetics import consumables, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import pass_view, season_pass


@pytest.fixture
def demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


@pytest.fixture
def season(active_season, session):
    session.add_all([Player(telegram_user_id=u) for u in (1, 2)])
    session.flush()
    return active_season


def _xp(session, season_id, player_id, amount) -> None:
    session.add(
        SeasonXp(season_id=season_id, player_id=player_id, amount=amount, source=XpSource.WIN)
    )
    session.flush()


def test_reached_counts_tiers_by_threshold(demo) -> None:
    xps = (0, 19, 20, 59, 60, 500)
    assert [season_pass.reached(demo, xp) for xp in xps] == [0, 0, 1, 1, 2, 3]


def test_sync_grants_each_free_tier_once(session, season, demo) -> None:
    _xp(session, season.id, 1, 60)
    granted = season_pass.sync(session, season, demo, 1)
    assert [(g.tier, g.premium) for g in granted] == [(1, False), (2, False)]
    assert inventory.owns(session, 1, "demo_1/name_flair.blossom")
    assert wallet.balance(session, 1, season_id=season.id) == 60
    assert season_pass.sync(session, season, demo, 1) == []


def test_crossing_several_tiers_at_once_grants_them_in_order(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    assert [g.tier for g in season_pass.sync(session, season, demo, 1)] == [1, 2, 3]
    assert consumables.count(session, 1, ConsumableKind.CLUE_TICKET) == 1


def test_premium_tiers_wait_for_premium(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    assert all(not g.premium for g in season_pass.sync(session, season, demo, 1))
    assert season_pass.row(session, season.id, 1).premium_claimed == 0


def test_an_already_owned_item_reward_still_claims_the_tier(session, season, demo) -> None:
    inventory.grant(session, 1, ItemGrant("demo_1/name_flair.blossom", ItemSource.ADMIN))
    _xp(session, season.id, 1, 20)
    (granted,) = season_pass.sync(session, season, demo, 1)
    assert granted.new is False
    assert season_pass.row(session, season.id, 1).free_claimed == 1


def test_no_tiers_granted_after_the_season_ended(session, season, demo) -> None:
    season.status = SeasonStatus.ENDED
    _xp(session, season.id, 1, 200)
    assert season_pass.sync(session, season, demo, 1) == []


def test_items_from_the_pass_remember_their_source_and_season(session, season, demo) -> None:
    _xp(session, season.id, 1, 20)
    season_pass.sync(session, season, demo, 1)
    owned = session.scalars(select(OwnedItem).where(OwnedItem.player_id == 1)).one()
    assert (owned.source, owned.season_id) == (ItemSource.TIER, season.id)


def test_a_season_win_grants_tiers_through_the_consumer(session, tagged_game, emit_win) -> None:
    emit_win(winner=1, stage=1)  # 50 XP in demo_1
    assert season_pass.row(session, tagged_game.season_id, 1).free_claimed == 1


def test_each_grant_is_logged(session, season, demo, log_records) -> None:
    _xp(session, season.id, 1, 20)
    season_pass.sync(session, season, demo, 1)
    assert any(r.level == "INFO" and "pass tier 1" in r.message for r in log_records)


def test_a_grant_queues_one_dm_listing_everything(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    season_pass.sync(session, season, demo, 1)
    (row,) = [r for r in outbox.pending(session, 10) if r.kind == OutboxKind.SEASON_TIER]
    assert row.payload["player_id"] == 1
    assert [g["tier"] for g in row.payload["granted"]] == [1, 2, 3]


def test_completing_the_free_track_queues_a_topic_line(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    season_pass.sync(session, season, demo, 1)
    kinds = [r.kind for r in outbox.pending(session, 10)]
    assert kinds.count(OutboxKind.SEASON_COMPLETE) == 1


def test_a_partial_grant_does_not_queue_the_topic_line(session, season, demo) -> None:
    _xp(session, season.id, 1, 60)
    season_pass.sync(session, season, demo, 1)
    assert [r.kind for r in outbox.pending(session, 10)] == [OutboxKind.SEASON_TIER]


def test_the_topic_line_is_queued_only_once(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    season_pass.sync(session, season, demo, 1)
    season_pass.sync(session, season, demo, 1)
    kinds = [r.kind for r in outbox.pending(session, 10)]
    assert kinds.count(OutboxKind.SEASON_COMPLETE) == 1


def test_nothing_new_queues_nothing(session, season, demo) -> None:
    season_pass.sync(session, season, demo, 1)
    assert outbox.pending(session, 10) == []


def test_describe_names_each_kind_of_reward(demo) -> None:
    assert season_pass.describe(demo, Reward.coins(60), "EN") == "60 🪙"
    assert season_pass.describe(demo, Reward.currency(50), "EN") == "50 💠"
    assert season_pass.describe(demo, Reward.clue_ticket(), "EN") == "🎟 Clue token"
    assert "Blossom" in season_pass.describe(demo, Reward.item("name_flair.blossom"), "EN")


def test_view_lines_show_progress_and_every_tier(session, season, demo) -> None:
    _xp(session, season.id, 1, 60)
    season_pass.sync(session, season, demo, 1)
    text = "\n".join(pass_view.view_lines(session, season, demo, 1, "EN"))
    assert "60 XP" in text
    assert "Tier 2/3" in text
    assert "next tier at 120 XP" in text
    assert "✅ 1." in text
    assert "▫️ 3." in text
    assert "🔒" in text  # premium not bought
    assert "Premium: 150 💠" in text


def test_view_lines_after_the_last_tier_have_no_next_line(session, season, demo) -> None:
    _xp(session, season.id, 1, 200)
    season_pass.sync(session, season, demo, 1)
    text = "\n".join(pass_view.view_lines(session, season, demo, 1, "EN"))
    assert "next tier" not in text


def test_view_lines_for_a_player_without_xp_show_an_empty_pass(session, season, demo) -> None:
    text = "\n".join(pass_view.view_lines(session, season, demo, 2, "EN"))
    assert "0 XP" in text
    assert "Tier 0/3" in text
    assert "✅" not in text
