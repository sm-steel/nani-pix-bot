"""The guide's example cards must be exactly what the bot itself would post."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.models.enums import (
    DEFAULT_ALGORITHM,
    CosmeticSlot,
    PixelStage,
    Rarity,
    RevealEffect,
)
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, mascot
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
from nani_pix_bot.services.cosmetics import looks, previews, stage
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG
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
    "season-sakura-plain-{lang}.webp",
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
    plain = gen_docs_cards.plain_season_banner(lang)
    assert (plain.headline, plain.subtitle) == ("", "")
    assert images[f"season-sakura-plain-{lang}.webp"] == gen_docs_cards.webp(
        render_season_banner(plain, key_visual)
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


# The Sakura cosmetics section (#409): frames as stills, win effects and transitions as clips.
FRAME_FILES = ("frame-paper.webp", "frame-gold-washi.webp", "frame-washi.webp")
CLIP_FILES = (
    "win-petal-drift-en.mp4",
    "win-petal-drift-ru.mp4",
    "win-sakura-storm-en.mp4",
    "win-sakura-storm-ru.mp4",
    "transition-spring-breeze.mp4",
    "transition-blossom-branch.mp4",
)
MAX_CLIP_BYTES = 600_000


def _sakura_items():
    run = registry.get("sakura_1")
    assert run is not None
    return run.items


def _slug(item) -> str:
    return item.key.split(".", 1)[1].replace("_", "-")


def test_cosmetic_file_names_come_from_the_runs_items_by_slot() -> None:
    items = _sakura_items()
    frames = {f"frame-{_slug(i)}.webp" for i in items if i.slot is CosmeticSlot.HOST_FRAME}
    wins = {
        f"win-{_slug(i)}-{lang}.mp4"
        for i in items
        if i.slot is CosmeticSlot.WIN_EFFECT
        for lang in LANGS
    }
    moves = {f"transition-{_slug(i)}.mp4" for i in items if i.slot is CosmeticSlot.HOST_TRANSITION}
    assert set(gen_docs_cards.frame_stills()) == frames == set(FRAME_FILES)
    assert set(gen_docs_cards.clip_jobs()) == wins | moves == set(CLIP_FILES)


def test_frame_stills_are_a_real_stage_3_sakura_image() -> None:
    stills = gen_docs_cards.frame_stills()
    key_visual = season_background("sakura_1")
    assert key_visual is not None
    width = DEFAULT_STAGE_CONFIG[PixelStage.STAGE_3].target_width
    for item in _sakura_items():
        if item.slot is not CosmeticSlot.HOST_FRAME:
            continue
        expected = mascot.add_to_png(
            stage.stage_png_with(
                key_visual, width, DEFAULT_ALGORITHM, item.look, stage.SeasonLook("watercolor")
            ),
            Pose.NOPE,
        )
        assert stills[f"frame-{_slug(item)}.webp"] == gen_docs_cards.webp(expected)
    assert all(len(data) <= MAX_SAKURA_IMAGE_BYTES for data in stills.values())


def test_clip_jobs_are_the_cosmetics_own_previews() -> None:
    jobs = gen_docs_cards.clip_jobs()
    for lang in LANGS:
        for name in ("petal-drift", "sakura-storm"):
            badge = jobs[f"win-{name}-{lang}.mp4"].badge
            assert badge is not None
            assert badge.handle == i18n.t("cosmetics.preview.you", lang)
            assert badge.overlay == name.replace("-", "_")
    assert jobs["transition-spring-breeze.mp4"] == previews.ClipJob(
        RevealEffect.SPRING_BREEZE, None
    )
    assert jobs["transition-blossom-branch.mp4"] == previews.ClipJob(
        RevealEffect.BLOSSOM_BRANCH, None
    )


def test_clip_image_is_the_key_visual_at_the_preview_size() -> None:
    with Image.open(BytesIO(gen_docs_cards.clip_image())) as image:
        assert image.size == previews.SAMPLE_SIZE


def test_generate_writes_the_frame_stills(tmp_path: Path) -> None:
    gen_docs_cards.generate(tmp_path)
    for name in FRAME_FILES:
        assert (tmp_path / name).stat().st_size > 0


@pytest.mark.ffmpeg
def test_generate_clips_writes_small_mp4s(tmp_path: Path) -> None:
    gen_docs_cards.generate_clips(tmp_path / "public")
    for name in CLIP_FILES:
        data = (tmp_path / "public" / name).read_bytes()
        assert data[4:8] == b"ftyp", name
        assert len(data) <= MAX_CLIP_BYTES, name
