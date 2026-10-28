import re
from dataclasses import replace
from pathlib import Path

import pytest

from nani_pix_bot.seasons import registry
from nani_pix_bot.services.seasons import season_guide
from nani_pix_bot.services.seasons.season_guide import GUIDE_URL

YOKAI = registry.discover("nani_pix_bot.seasons.runs")["yokai_1"]
DEMO = registry.discover("tests.seasons.fake_runs")["demo_1"]  # no optional mechanics
DOCS = Path(__file__).parents[3] / "docs" / "site" / "src" / "content" / "docs"
LINK = re.compile(r"\]\((https://[^)]+)\)")


def _guide(run=YOKAI, lang="EN", *, started=True) -> str:
    return season_guide.markdown(run, lang, started=started)


@pytest.mark.parametrize("lang", ["EN", "RU"])
@pytest.mark.parametrize("started", [True, False])
def test_every_link_points_at_a_real_guide_page(lang, started) -> None:
    links = LINK.findall(_guide(lang=lang, started=started))
    assert links
    for url in links:
        assert url.startswith(GUIDE_URL)
        path = url.removeprefix(GUIDE_URL).split("#")[0].strip("/")
        assert (DOCS / f"{path}.mdx").is_file(), url
        assert ("/ru/" in url) == (lang == "RU"), url


@pytest.mark.parametrize("lang", ["EN", "RU"])
def test_every_mechanic_of_the_run_gets_its_section(lang) -> None:
    text = _guide(lang=lang)
    for page in (
        "seasons/season-pass/",
        "seasons/haunted-rounds/",
        "seasons/hundred-candles/",
        "seasons/hexes/",
        "seasons/omikuji-and-charms/",
        "seasons/yokai-night/",
    ):
        assert page in text, page


def test_a_run_without_mechanics_skips_their_sections() -> None:
    text = _guide(DEMO)
    for page in ("haunted-rounds", "hundred-candles", "hexes", "omikuji-and-charms"):
        assert page not in text, page
    for command in ("/hex", "/omikuji", "/candles", "/inventory"):
        assert command not in text, command
    assert "/seasonpass" in text


def test_the_numbers_come_from_the_run() -> None:
    text = _guide()
    for rule in YOKAI.hexes:
        assert f"| {rule.price} |" in text
    total = sum(o.weight for o in YOKAI.omikuji)
    for outcome in YOKAI.omikuji:
        assert f"| {outcome.weight * 100 // total}% |" in text
    assert f"| {YOKAI.coins.win} |" in text
    assert YOKAI.group_goal is not None
    assert f"{YOKAI.group_goal.contributor_coins} 🪙" in text


def test_the_boss_coin_multiplier_includes_the_haunting() -> None:
    assert YOKAI.group_goal is not None
    assert YOKAI.haunt is not None
    total = YOKAI.group_goal.boss_coin_multiplier * YOKAI.haunt.coin_multiplier
    assert f"\N{MULTIPLICATION SIGN}{total} 🪙" in _guide()


def test_teaser_and_start_open_differently() -> None:
    teaser, start = _guide(started=False), _guide(started=True)
    assert teaser.splitlines()[0] != start.splitlines()[0]


def test_only_the_teaser_states_the_gate_since_the_start_caption_already_does() -> None:
    assert YOKAI.gate is not None
    rule = YOKAI.gate.description["EN"]
    assert rule in _guide(started=False)
    assert rule not in _guide(started=True)


def test_the_run_name_is_escaped() -> None:
    run = replace(YOKAI, names={"EN": "Night (beta).", "RU": "Ночь"})
    assert "Night \\(beta\\)\\." in _guide(run)


@pytest.mark.parametrize("lang", ["EN", "RU"])
@pytest.mark.parametrize("started", [True, False])
def test_the_guide_fits_the_plain_text_fallback(lang, started) -> None:
    # A rejected rich message goes out as plain text, cut at 4096 characters.
    assert len(_guide(lang=lang, started=started)) <= 4096
