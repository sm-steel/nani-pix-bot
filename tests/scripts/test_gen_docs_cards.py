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


SAKURA = gen_docs_cards.DOCS_RUNS[0]
YOKAI = gen_docs_cards.DOCS_RUNS[1]
RUNS = pytest.mark.parametrize("docs", gen_docs_cards.DOCS_RUNS, ids=lambda d: d.slug)
# Today's published Sakura names: the guide pages link them, so they must never change.
SAKURA_NAMES = {
    "season-sakura-{lang}.webp",
    "season-sakura-plain-{lang}.webp",
    "profile-stamps-{lang}.webp",
    "profile-night-hanami-{lang}.webp",
    "profile-blossom-avenue-{lang}.webp",
    "profile-school-rooftop-{lang}.webp",
}
SAKURA_STILLS = {"frame-paper.webp", "frame-gold-washi.webp", "frame-washi.webp"}
SAKURA_CLIPS = {
    "win-petal-drift-en.mp4",
    "win-petal-drift-ru.mp4",
    "win-sakura-storm-en.mp4",
    "win-sakura-storm-ru.mp4",
    "transition-spring-breeze.mp4",
    "transition-blossom-branch.mp4",
}
MAX_IMAGE_BYTES = 300_000
MAX_CLIP_BYTES = 600_000


def _items(docs):
    found = registry.get(docs.run_id)
    assert found is not None
    return found.items


def _slug(item) -> str:
    return item.key.split(".", 1)[1].replace("_", "-")


def _file_names(docs) -> set[str]:
    """Every file the generator writes for `docs`, both languages."""
    names = set(gen_docs_cards.frame_stills(docs)) | set(gen_docs_cards.costume_stills(docs))
    names |= set(gen_docs_cards.clip_jobs(docs))
    for lang in LANGS:
        names |= set(gen_docs_cards.season_images(docs, lang))
    return names


def test_docs_runs_are_the_two_runs() -> None:
    assert [
        (d.run_id, d.slug, d.title_key, d.run_badge, d.dates) for d in gen_docs_cards.DOCS_RUNS
    ] == [
        (
            "sakura_1",
            "sakura",
            "title.hanami_host",
            ("hanami_party", Rarity.BRONZE),
            "12.10 18:00 \u2013 22.10 18:00 (Europe/Moscow)",
        ),
        (
            "yokai_1",
            "yokai",
            "title.onmyoji",
            ("gashadokuro", Rarity.SILVER),
            "28.10 06:00 \u2013 12.11 06:00 (Europe/Moscow)",
        ),
    ]


def test_generated_file_names_never_collide_across_runs() -> None:
    assert not _file_names(SAKURA) & _file_names(YOKAI)


def test_sakura_file_names_are_unchanged() -> None:
    expected = {name.format(lang=lang) for name in SAKURA_NAMES for lang in LANGS}
    assert _file_names(SAKURA) == expected | SAKURA_STILLS | SAKURA_CLIPS


def test_costume_stills_are_one_per_costume_item() -> None:
    for docs in gen_docs_cards.DOCS_RUNS:
        costumes = {
            f"costume-{_slug(i)}.webp"
            for i in _items(docs)
            if i.slot is CosmeticSlot.MASCOT_COSTUME
        }
        assert set(gen_docs_cards.costume_stills(docs)) == costumes
    assert not gen_docs_cards.costume_stills(SAKURA)
    assert len(gen_docs_cards.costume_stills(YOKAI)) == 5


def test_costume_still_is_nani_in_that_costume_on_stage_3() -> None:
    still = gen_docs_cards.costume_stills(YOKAI)["costume-chochin-obake.webp"]
    key_visual = season_background("yokai_1")
    assert key_visual is not None
    width = DEFAULT_STAGE_CONFIG[PixelStage.STAGE_3].target_width
    look = stage.SeasonLook("moonlit")
    base = stage.stage_png_with(key_visual, width, DEFAULT_ALGORITHM, None, look)
    assert still == gen_docs_cards.webp(mascot.add_to_png(base, Pose.NOPE, "chochin"))


@RUNS
def test_run_banner_files_are_the_runs_banners(docs) -> None:
    expected = tuple(i.look for i in _items(docs) if i.slot is CosmeticSlot.PROFILE_BANNER)
    assert gen_docs_cards.banner_files(docs) == expected
    assert len(expected) == 3


@RUNS
@pytest.mark.parametrize("lang", LANGS)
def test_season_start_card_is_the_bots_start_post(docs, lang: str) -> None:
    banner = gen_docs_cards.season_banner(docs, lang)
    assert banner.headline == i18n.t("card.season.season_start", lang)
    season = registry.get(docs.run_id)
    assert season is not None
    assert banner.title == season.name(lang.upper())
    assert banner.subtitle == docs.dates
    assert banner.stamp == season_stamp(docs.run_id, plus=False)


@RUNS
@pytest.mark.parametrize("lang", LANGS)
def test_stamps_card_shows_plain_gold_ring_and_plus(docs, lang: str) -> None:
    stamps = gen_docs_cards.stamps_card(docs, lang).stamps
    assert [s.gold for s in stamps] == [False, True, True]
    assert [s.art for s in stamps] == [
        season_stamp(docs.run_id, plus=False),
        season_stamp(docs.run_id, plus=False),
        season_stamp(docs.run_id, plus=True),
    ]


@RUNS
@pytest.mark.parametrize("lang", LANGS)
def test_season_images_are_the_production_renders(docs, lang: str) -> None:
    images = gen_docs_cards.season_images(docs, lang)
    key_visual = season_background(docs.run_id)
    slug = docs.slug
    assert images[f"season-{slug}-{lang}.webp"] == gen_docs_cards.webp(
        render_season_banner(gen_docs_cards.season_banner(docs, lang), key_visual)
    )
    plain = gen_docs_cards.plain_season_banner(docs, lang)
    assert (plain.headline, plain.subtitle) == ("", "")
    assert images[f"season-{slug}-plain-{lang}.webp"] == gen_docs_cards.webp(
        render_season_banner(plain, key_visual)
    )
    stamps = render_profile_card(gen_docs_cards.stamps_card(docs, lang), None, key_visual)
    assert images[gen_docs_cards.stamps_file(docs, lang)] == gen_docs_cards.webp(stamps)
    for file_name in gen_docs_cards.banner_files(docs):
        banner = looks.banner_path(docs.run_id, file_name).read_bytes()
        card = render_profile_card(gen_docs_cards.banner_card(docs, lang), None, banner)
        stem = gen_docs_cards.banner_stem(file_name)
        assert images[f"profile-{stem}-{lang}.webp"] == gen_docs_cards.webp(card)
    assert len(images) == 3 + len(gen_docs_cards.banner_files(docs))
    assert all(len(data) <= MAX_IMAGE_BYTES for data in images.values())


def test_stamps_card_file_names() -> None:
    assert gen_docs_cards.stamps_file(YOKAI, "en") == "profile-yokai-stamps-en.webp"
    assert gen_docs_cards.stamps_file(SAKURA, "ru") == "profile-stamps-ru.webp"


def test_generate_writes_every_runs_images(tmp_path: Path) -> None:
    gen_docs_cards.generate(tmp_path)
    for docs in gen_docs_cards.DOCS_RUNS:
        stills = set(gen_docs_cards.frame_stills(docs)) | set(gen_docs_cards.costume_stills(docs))
        for lang in LANGS:
            stills |= set(gen_docs_cards.season_images(docs, lang))
        for name in stills:
            assert (tmp_path / name).stat().st_size > 0, name


@RUNS
def test_cosmetic_file_names_come_from_the_runs_items_by_slot(docs) -> None:
    items = _items(docs)
    frames = {f"frame-{_slug(i)}.webp" for i in items if i.slot is CosmeticSlot.HOST_FRAME}
    wins = {
        f"win-{_slug(i)}-{lang}.mp4"
        for i in items
        if i.slot is CosmeticSlot.WIN_EFFECT
        for lang in LANGS
    }
    moves = {f"transition-{_slug(i)}.mp4" for i in items if i.slot is CosmeticSlot.HOST_TRANSITION}
    assert set(gen_docs_cards.frame_stills(docs)) == frames
    assert set(gen_docs_cards.clip_jobs(docs)) == wins | moves


def test_yokai_media_is_the_registry_keys() -> None:
    frames = ("ofuda-strip", "cobweb", "lantern-string", "bone-border")
    assert set(gen_docs_cards.frame_stills(YOKAI)) == {f"frame-{k}.webp" for k in frames}
    jobs = gen_docs_cards.clip_jobs(YOKAI)
    assert {n for n in jobs if n.startswith("transition-")} == {
        "transition-candle-snuff.mp4",
        "transition-night-parade.mp4",
        "transition-gashadokuro-rise.mp4",
    }
    assert jobs["transition-candle-snuff.mp4"] == previews.ClipJob(RevealEffect.CANDLE_SNUFF, None)
    assert {n for n in jobs if n.startswith("win-") and n.endswith("-en.mp4")} == {
        "win-bat-burst-en.mp4",
        "win-hitodama-en.mp4",
        "win-bone-rattle-en.mp4",
    }


@RUNS
def test_frame_stills_are_a_real_stage_3_image(docs) -> None:
    stills = gen_docs_cards.frame_stills(docs)
    key_visual = season_background(docs.run_id)
    assert key_visual is not None
    width = DEFAULT_STAGE_CONFIG[PixelStage.STAGE_3].target_width
    season = registry.get(docs.run_id)
    assert season is not None
    for item in _items(docs):
        if item.slot is not CosmeticSlot.HOST_FRAME:
            continue
        expected = mascot.add_to_png(
            stage.stage_png_with(
                key_visual, width, DEFAULT_ALGORITHM, item.look, stage.SeasonLook(season.style)
            ),
            Pose.NOPE,
        )
        assert stills[f"frame-{_slug(item)}.webp"] == gen_docs_cards.webp(expected)
    assert all(len(data) <= MAX_IMAGE_BYTES for data in stills.values())


def test_clip_jobs_are_the_cosmetics_own_previews() -> None:
    jobs = gen_docs_cards.clip_jobs(SAKURA)
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


@RUNS
def test_clip_image_is_the_key_visual_at_the_preview_size(docs) -> None:
    with Image.open(BytesIO(gen_docs_cards.clip_image(docs))) as image:
        assert image.size == previews.SAMPLE_SIZE


@pytest.mark.ffmpeg
def test_generate_clips_writes_small_mp4s(tmp_path: Path) -> None:
    gen_docs_cards.generate_clips(tmp_path / "public")
    for docs in gen_docs_cards.DOCS_RUNS:
        for name in gen_docs_cards.clip_jobs(docs):
            data = (tmp_path / "public" / name).read_bytes()
            assert data[4:8] == b"ftyp", name
            assert len(data) <= MAX_CLIP_BYTES, name
