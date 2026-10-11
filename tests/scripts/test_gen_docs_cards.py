"""The guide's example cards must be exactly what the bot itself would post."""

from pathlib import Path

import pytest

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements.rewards import POINTS
from nani_pix_bot.services.cards import (
    load_background,
    podium_slot,
    render_podium_card,
    render_profile_card,
    render_season_banner,
    render_unlock_card,
    season_background,
    season_stamp,
    unlock_slot,
)
from nani_pix_bot.services.cosmetics import looks
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey
from scripts import gen_docs_cards

LANGS = ("en", "ru")


@pytest.mark.parametrize("lang", LANGS)
def test_unlock_card_reads_like_the_bots_announcement(lang: str) -> None:
    card = gen_docs_cards.unlock_card(lang)
    assert card.rarity is Rarity.GOLD
    assert card.headline == i18n.t("card.unlocked", lang)
    assert card.name == i18n.t("achievement.sharpshooter.name", lang) + " V"
    assert card.description == i18n.t("achievement.sharpshooter.desc", lang, n=50)
    assert card.rarity_label == i18n.t("achievement.rarity.gold", lang)
    assert card.reward == DEFAULT_AMOUNTS[EconomyKey.ACHIEVEMENT_GOLD]
    assert card.points_label == i18n.t("card.points", lang, points=POINTS[Rarity.GOLD])


@pytest.mark.parametrize("lang", LANGS)
def test_podium_card_is_a_yearly_one(lang: str) -> None:
    card = gen_docs_cards.podium_card(lang)
    assert card.title == i18n.t("card.podium.year", lang, period=gen_docs_cards.YEAR)
    assert [entry.rank for entry in card.entries] == [1, 2, 3]
    for entry, (name, score, wins) in zip(card.entries, gen_docs_cards.PODIUM, strict=True):
        assert entry.name == name
        assert entry.score_label == i18n.t("card.score", lang, score=score, wins=wins)


def test_writes_the_bots_own_renders_per_language(tmp_path: Path) -> None:
    gen_docs_cards.generate(tmp_path / "nested")
    out = tmp_path / "nested"
    for lang in LANGS:
        unlock = gen_docs_cards.unlock_card(lang)
        background = load_background(unlock_slot(Rarity.GOLD), gen_docs_cards.UNLOCK_SEED)
        assert (out / f"unlock-{lang}.png").read_bytes() == render_unlock_card(
            unlock, None, background
        )
        podium = gen_docs_cards.podium_card(lang)
        background = load_background(podium_slot("year"), podium.entries[0].seed)
        assert (out / f"podium-{lang}.png").read_bytes() == render_podium_card(podium, background)


SAKURA_FILES = (
    "season-sakura-{lang}.webp",
    "profile-stamps-{lang}.webp",
    "profile-night-hanami-{lang}.webp",
    "profile-blossom-avenue-{lang}.webp",
    "profile-school-rooftop-{lang}.webp",
)
MAX_SAKURA_IMAGE_BYTES = 300_000


def test_sakura_banner_files_are_the_runs_three_banners() -> None:
    assert gen_docs_cards.banner_files() == (
        "night_hanami.jpg",
        "blossom_avenue.jpg",
        "school_rooftop.jpg",
    )


@pytest.mark.parametrize("lang", LANGS)
def test_season_start_card_is_the_bots_start_post(lang: str) -> None:
    banner = gen_docs_cards.season_banner(lang)
    assert banner.headline == i18n.t("card.season.season_start", lang)
    assert banner.title == ("Sakura Season" if lang == "en" else "Сезон сакуры")
    assert banner.stamp == season_stamp("sakura_1", plus=False)


@pytest.mark.parametrize("lang", LANGS)
def test_stamps_card_shows_plain_gold_ring_and_sakura_plus(lang: str) -> None:
    stamps = gen_docs_cards.stamps_card(lang).stamps
    assert [s.gold for s in stamps] == [False, True, True]
    assert [s.art for s in stamps] == [
        season_stamp("sakura_1", plus=False),
        season_stamp("sakura_1", plus=False),
        season_stamp("sakura_1", plus=True),
    ]


@pytest.mark.parametrize("lang", LANGS)
def test_sakura_images_are_the_production_renders(lang: str) -> None:
    images = gen_docs_cards.sakura_images(lang)
    assert set(images) == {name.format(lang=lang) for name in SAKURA_FILES}
    key_visual = season_background("sakura_1")
    assert images[f"season-sakura-{lang}.webp"] == gen_docs_cards.webp(
        render_season_banner(gen_docs_cards.season_banner(lang), key_visual)
    )
    stamps = render_profile_card(gen_docs_cards.stamps_card(lang), None, key_visual)
    assert images[f"profile-stamps-{lang}.webp"] == gen_docs_cards.webp(stamps)
    for file_name in gen_docs_cards.banner_files():
        banner = looks.banner_path("sakura_1", file_name).read_bytes()
        card = render_profile_card(gen_docs_cards.banner_card(lang), None, banner)
        stem = gen_docs_cards.banner_stem(file_name)
        assert images[f"profile-{stem}-{lang}.webp"] == gen_docs_cards.webp(card)
    assert all(len(data) <= MAX_SAKURA_IMAGE_BYTES for data in images.values())


def test_generate_writes_the_sakura_images(tmp_path: Path) -> None:
    gen_docs_cards.generate(tmp_path)
    for lang in LANGS:
        for name in SAKURA_FILES:
            assert (tmp_path / name.format(lang=lang)).stat().st_size > 0
