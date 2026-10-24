import pytest

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import haunting
from tests.seasons_fixtures import FixedRng


def _run(run_id: str) -> SeasonRun:
    run = registry.get(run_id)
    assert run is not None
    return run


@pytest.fixture
def demo1() -> SeasonRun:
    return _run("demo_1")


@pytest.fixture
def demo3() -> SeasonRun:
    return _run("demo_3")


def test_roll_haunts_below_the_chance(demo3) -> None:
    assert (
        haunting.roll(demo3, event_day=False, rng=FixedRng(randrange=24, choice=0))
        is HauntTwist.KITSUNE_MIRROR
    )
    assert haunting.roll(demo3, event_day=False, rng=FixedRng(randrange=25)) is None


def test_event_day_with_all_haunted_always_haunts(demo3) -> None:
    assert (
        haunting.roll(demo3, event_day=True, rng=FixedRng(randrange=99, choice=2))
        is HauntTwist.YUREI_DOUBLE
    )


def test_a_run_without_haunt_never_rolls(demo1) -> None:
    assert haunting.roll(demo1, event_day=True, rng=FixedRng(randrange=0)) is None


def test_twist_for_stage_uses_the_stored_twist(demo3, haunted_game) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"
    got = [haunting.twist_for_stage(demo3, haunted_game, s) for s in (1, 5)]
    assert got == [HauntTwist.TANUKI_LEAF] * 2


def test_boss_game_cycles_the_run_twists_by_stage(demo3, haunted_game) -> None:
    haunted_game.boss_game = True
    haunted_game.haunt_twist = "tanuki_leaf"
    got = [haunting.twist_for_stage(demo3, haunted_game, s) for s in range(1, 6)]
    assert got == [*demo3.haunt.twists, demo3.haunt.twists[0]]


def test_no_run_or_no_haunt_means_no_twist(demo1, haunted_game) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    assert haunting.twist_for_stage(None, haunted_game, 1) is None
    assert haunting.twist_for_stage(demo1, haunted_game, 1) is None


def test_unknown_stored_twist_is_ignored(haunted_game, log_records) -> None:
    haunted_game.haunt_twist = "nurikabe_wall"
    assert haunting.stored_twist(haunted_game) is None
    assert any(
        r.level == "WARNING" and r.extra.get("twist") == "nurikabe_wall" for r in log_records
    )


def test_is_haunted_counts_the_boss(haunted_game) -> None:
    assert not haunting.is_haunted(haunted_game)
    haunted_game.haunt_twist = "yurei_double"
    assert haunting.is_haunted(haunted_game)
    haunted_game.haunt_twist = None
    haunted_game.boss_game = True
    assert haunting.is_haunted(haunted_game)


def test_only_the_mirror_stays_on_the_last_stage() -> None:
    assert set(HauntTwist) - {HauntTwist.KITSUNE_MIRROR} == haunting.FADES_AT_LAST


def test_first_stage_announces_the_twist_and_multiplier(session, haunted_game) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    line = haunting.announcement(session, haunted_game, "en", number=1, total=5)
    assert line is not None
    assert "Kitsune illusion" in line
    assert "\N{MULTIPLICATION SIGN}2" in line


def test_middle_stage_is_silent(session, haunted_game) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    assert haunting.announcement(session, haunted_game, "en", number=3, total=5) is None


def test_fading_twist_announces_lifting_on_the_last_stage(session, haunted_game) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"
    line = haunting.announcement(session, haunted_game, "en", number=5, total=5)
    assert line is not None
    assert "lifts" in line


def test_yurei_double_announces_lifting_on_the_last_stage(session, haunted_game) -> None:
    haunted_game.haunt_twist = "yurei_double"
    line = haunting.announcement(session, haunted_game, "en", number=5, total=5)
    assert line is not None
    assert "Yūrei double" in line
    assert "lifts" in line


def test_mirror_does_not_announce_on_the_last_stage(session, haunted_game) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    assert haunting.announcement(session, haunted_game, "en", number=5, total=5) is None


def test_boss_game_announces_each_new_twist(session, haunted_game, demo3) -> None:
    haunted_game.boss_game = True
    line = haunting.announcement(session, haunted_game, "en", number=2, total=5)
    assert line is not None
    assert "new haunting" in line
    assert i18n.t(f"haunt.twist.{demo3.haunt.twists[1].value}", "en") in line


def test_unhaunted_game_gets_no_line(session, tagged_game) -> None:
    for number in (1, 5):
        assert haunting.announcement(session, tagged_game, "en", number=number, total=5) is None


def test_caption_suffix_is_a_newline_line_or_nothing(session, haunted_game) -> None:
    assert haunting.caption_suffix(session, haunted_game, "en", number=1, total=5) == ""
    haunted_game.haunt_twist = "kitsune_mirror"
    suffix = haunting.caption_suffix(session, haunted_game, "en", number=1, total=5)
    assert suffix.startswith("\n")
    assert "Kitsune illusion" in suffix
