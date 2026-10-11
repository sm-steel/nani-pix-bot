from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.seasons.definition import Modifier
from nani_pix_bot.services.seasons import modifiers


def test_a_tagged_game_gets_the_run_modifiers(session, tagged_game) -> None:
    assert modifiers.for_game(session, tagged_game) == {Modifier.CLUE_DISCOUNT_PCT: 25}


def test_an_untagged_game_gets_none(session, untagged_game) -> None:
    assert modifiers.for_game(session, untagged_game) == {}


def test_a_closing_season_keeps_them(session, tagged_game, active_season) -> None:
    active_season.status = SeasonStatus.CLOSING
    assert modifiers.for_game(session, tagged_game)[Modifier.CLUE_DISCOUNT_PCT] == 25


def test_an_unregistered_run_gives_nothing_and_warns(
    session, tagged_game, active_season, log_records
) -> None:
    active_season.run_id = "gone_1"
    session.flush()

    assert modifiers.season_of(session, tagged_game) is None
    [warning] = [r for r in log_records if r.level == "WARNING"]
    assert warning.extra["run_id"] == "gone_1"
    assert warning.extra["game_id"] == tagged_game.id
