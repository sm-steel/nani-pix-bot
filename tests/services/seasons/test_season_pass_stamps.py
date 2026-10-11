import pytest

from nani_pix_bot.models.enums import XpSource
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Reward
from nani_pix_bot.services.seasons import season_pass


@pytest.fixture
def demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def test_reaching_the_stamp_tier_claims_it_without_a_delivery(
    session, active_season, demo, log_records
) -> None:
    session.add(Player(telegram_user_id=1))
    session.add(SeasonXp(season_id=active_season.id, player_id=1, amount=500, source=XpSource.WIN))
    session.flush()
    granted = season_pass.sync(session, active_season, demo, 1)
    assert granted[-1].reward == Reward.stamp()
    assert granted[-1].new is True
    assert season_pass.row(session, active_season.id, 1).free_claimed == len(demo.tiers)
    stamp_lines = [r for r in log_records if r.level == "INFO" and "stamp" in r.message]
    assert len(stamp_lines) == 1  # the tier grant line only, naming the recipient
    assert stamp_lines[0].extra["recipient_id"] == 1


def test_the_stamp_texts(demo) -> None:
    assert season_pass.describe(demo, Reward.stamp(), "EN") == "the season stamp's gold ring"
    assert season_pass.describe(demo, Reward.stamp(), "RU") == "золотое кольцо сезонного штампа"
    assert season_pass.describe(demo, Reward.stamp_plus(), "EN") == "the gold season stamp"
    assert season_pass.describe(demo, Reward.stamp_plus(), "RU") == "золотой сезонный штамп"
