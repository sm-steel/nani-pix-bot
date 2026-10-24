import pickle
from collections.abc import Iterator
from dataclasses import replace
from io import BytesIO
from types import MappingProxyType

import pytest
from PIL import Image, ImageOps

from nani_pix_bot.models.cosmetics import ITEM_ID_LENGTH, ItemPreview
from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm, PreviewKind, RevealEffect
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.services import mascot
from nani_pix_bot.services.cards import CARD_SIZE, ProfileCard
from nani_pix_bot.services.cosmetics import looks, previews, stage
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.reveal import pipeline
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG
from tests.costume_fixtures import ORANGE

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
AMY = previews.Viewer(7, handle="@amy", full_name="Amy Lee", avatar=b"avatar-bytes")


def _resolved(key: str) -> registry.ResolvedItem:
    resolved = registry.item(f"demo_1/{key}")
    assert resolved is not None
    return resolved


@pytest.fixture
def banner(monkeypatch, tmp_path) -> registry.ResolvedItem:
    """The demo run extended with a banner whose PNG is generated under tmp_path."""
    banners = tmp_path / "demo_1" / "banners"
    banners.mkdir(parents=True)
    Image.new("RGB", (16, 9), (10, 20, 200)).save(banners / "sky.png")
    monkeypatch.setattr(looks, "SEASON_ART_DIR", tmp_path)
    item = Item(
        "profile_banner.sky",
        CosmeticSlot.PROFILE_BANNER,
        "sky.png",
        MappingProxyType({"EN": "Sky", "RU": "Sky"}),
    )
    demo = registry.all_runs()["demo_1"]
    runs = {"demo_1": replace(demo, items=(*demo.items, item))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    return _resolved("profile_banner.sky")


@pytest.fixture
def clip_calls(monkeypatch) -> list[tuple]:
    """Stubs the reveal pipeline: no ffmpeg in unit tests."""
    calls: list[tuple] = []

    def fake_clip(*args) -> bytes:
        calls.append(args)
        return b"mp4"

    monkeypatch.setattr(pipeline, "preview_clip", fake_clip)
    return calls


def test_sample_stage_is_a_640x360_png_drawn_once() -> None:
    first = previews.sample_stage()
    assert first.startswith(PNG_MAGIC)
    assert Image.open(BytesIO(first)).size == (640, 360)
    assert previews.sample_stage() is first


def test_sample_stage_is_the_committed_screenshot_fitted_to_size() -> None:
    """The neutral sample anime screenshot (#424), not a drawn scene."""
    assert previews.SAMPLE_SCREENSHOT.is_file()
    with Image.open(previews.SAMPLE_SCREENSHOT) as source:
        expected = ImageOps.fit(source.convert("RGB"), previews.SAMPLE_SIZE)
    sample = Image.open(BytesIO(previews.sample_stage())).convert("RGB")
    assert sample.tobytes() == expected.tobytes()


def test_sample_stage_is_not_a_flat_colour() -> None:
    image = Image.open(BytesIO(previews.sample_stage())).convert("RGB")
    colours = image.getcolors(maxcolors=640 * 360)
    assert colours is not None
    assert len(colours) > 50


def test_flair_previews_as_text_with_the_emoji() -> None:
    preview = previews.render(_resolved("name_flair.blossom"), "EN", AMY)
    assert preview.kind is PreviewKind.TEXT
    assert preview.text == "Amy Lee 🌸"  # the full name, as flair.named shows it in the group


def test_title_previews_as_text_in_quotes() -> None:
    assert previews.render(_resolved("title.demo"), "EN", AMY).text == "«Demo Star»"
    assert previews.render(_resolved("title.demo"), "RU", AMY).text == "«Демо-звезда»"


def test_reaction_previews_as_text() -> None:
    preview = previews.render(_resolved("guess_reaction.love"), "EN", AMY)
    assert preview.kind is PreviewKind.TEXT
    assert preview.text == "🥰 on your winning guess"


def test_frame_previews_as_a_framed_sample_png() -> None:
    preview = previews.render(_resolved("host_frame.border"), "EN", AMY)
    assert preview.kind is PreviewKind.PHOTO
    assert preview.image is not None
    assert preview.image.startswith(PNG_MAGIC)
    assert preview.image != previews.sample_stage()
    framed = Image.open(BytesIO(preview.image)).convert("RGB")
    assert framed.size == (640, 360)
    assert framed.getpixel((0, 0)) == (245, 245, 245)  # simple_border's colour


def test_a_frame_previews_on_a_stage_like_a_real_game_posts() -> None:
    """Pixelated to stage 3, in the item's season style, framed, then Nani in stage 3's
    pose: what the frame looks like on a real stage post, not on a clear picture."""
    resolved = _resolved("host_frame.border")
    preview = previews.render(resolved, "EN", AMY)
    framed = stage.stage_png_with(
        previews.sample_stage(),
        previews.FRAME_STAGE_WIDTH,
        PixelAlgorithm.MEDIAN,
        resolved.item.look,
        resolved.run.style,
    )
    expected = mascot.add_to_png(framed, Pose.NOPE)
    assert preview.image == expected


def test_banner_previews_as_a_profile_card(banner) -> None:
    preview = previews.render(banner, "EN", AMY)
    assert preview.kind is PreviewKind.PHOTO
    assert preview.image is not None
    assert Image.open(BytesIO(preview.image)).size == CARD_SIZE


def test_banner_previews_on_the_viewers_own_card(banner, monkeypatch) -> None:
    """Their real card (name, title, stamps, stats) and avatar, with the new
    banner behind it: exactly what equipping it changes."""
    drawn = []
    monkeypatch.setattr(previews, "render_profile_card", lambda *args: drawn.append(args) or b"png")
    own = ProfileCard(name="@amy", title="Senpai", stamps=(), stats=(), seed=7)
    previews.render(banner, "EN", replace(AMY, card=own))
    [(card, avatar, background)] = drawn
    assert card is own
    assert avatar == b"avatar-bytes"
    assert background == looks.banner_path("demo_1", "sky.png").read_bytes()


def test_transition_previews_as_a_clip_with_that_effect_and_no_badge(clip_calls) -> None:
    preview = previews.render(_resolved("host_transition.iris"), "EN", AMY)
    assert preview.kind is PreviewKind.VIDEO
    assert preview.video == b"mp4"
    [(image, algorithm, widths, effect, ending)] = clip_calls
    assert image == previews.sample_stage()
    assert algorithm is PixelAlgorithm.MEDIAN
    assert widths == tuple(s.target_width for s in DEFAULT_STAGE_CONFIG.values())
    assert effect is RevealEffect.IRIS
    assert ending == pipeline.Ending(None, pipeline.Nani(Pose.WIN))


def test_win_effect_previews_as_an_iris_clip_with_the_overlay_badge(clip_calls) -> None:
    preview = previews.render(_resolved("win_effect.confetti"), "EN", AMY)
    assert preview.kind is PreviewKind.VIDEO
    [(_image, _algorithm, _widths, effect, ending)] = clip_calls
    assert effect is RevealEffect.IRIS
    assert ending.badge == pipeline.Badge(b"avatar-bytes", "@amy", overlay="confetti")


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        # The previews that show Nani (issue #461) carry "#nani", so copies cached before
        # her are never served again.
        ("host_frame.border", "demo_1/host_frame.border#nani"),  # the same for everyone
        ("host_transition.iris", "demo_1/host_transition.iris#nani"),
        ("win_effect.confetti", "demo_1/win_effect.confetti@7#nani"),  # their avatar and name
        ("name_flair.blossom", "demo_1/name_flair.blossom"),  # text: never stored anyway
    ],
)
def test_cache_key_is_per_viewer_only_where_the_viewer_shows(key, expected) -> None:
    assert previews.cache_key(_resolved(key), 7) == expected


def test_a_banner_preview_is_never_cached(banner) -> None:
    assert previews.cache_key(banner, 7) is None  # their stats move on


def test_clip_job_is_none_for_a_still_preview() -> None:
    assert previews.clip_job(_resolved("host_frame.border"), AMY) is None
    assert previews.clip_job(_resolved("title.demo"), AMY) is None


def test_clip_job_args_are_picklable() -> None:
    job = previews.clip_job(_resolved("win_effect.confetti"), AMY)
    assert job is not None
    assert pickle.dumps(job.args())  # they cross into the reveal worker process


def test_nothing_cached_yet(session) -> None:
    assert previews.cached(session, "demo_1/host_frame.border", "EN") is None


def test_remember_then_cached_roundtrip(session) -> None:
    previews.remember(session, "demo_1/host_frame.border", "EN", PreviewKind.PHOTO, "file-1")
    row = previews.cached(session, "demo_1/host_frame.border", "EN")
    assert isinstance(row, ItemPreview)
    assert (row.kind, row.file_id) == (PreviewKind.PHOTO, "file-1")


def test_remember_replaces_an_older_file_id(session) -> None:
    previews.remember(session, "demo_1/host_frame.border", "EN", PreviewKind.PHOTO, "file-1")
    previews.remember(session, "demo_1/host_frame.border", "EN", PreviewKind.PHOTO, "file-2")
    row = previews.cached(session, "demo_1/host_frame.border", "EN")
    assert row is not None
    assert row.file_id == "file-2"


def test_cached_previews_are_kept_per_language(session) -> None:
    previews.remember(session, "demo_1/host_frame.border", "EN", PreviewKind.PHOTO, "file-en")
    assert previews.cached(session, "demo_1/host_frame.border", "RU") is None
    previews.remember(session, "demo_1/host_frame.border", "RU", PreviewKind.PHOTO, "file-ru")
    en = previews.cached(session, "demo_1/host_frame.border", "EN")
    ru = previews.cached(session, "demo_1/host_frame.border", "RU")
    assert en is not None
    assert ru is not None
    assert (en.file_id, ru.file_id) == ("file-en", "file-ru")


def test_forget_drops_only_that_language(session) -> None:
    previews.remember(session, "demo_1/host_frame.border", "EN", PreviewKind.PHOTO, "file-en")
    previews.remember(session, "demo_1/host_frame.border", "RU", PreviewKind.PHOTO, "file-ru")
    previews.forget(session, "demo_1/host_frame.border", "EN")
    assert previews.cached(session, "demo_1/host_frame.border", "EN") is None
    assert previews.cached(session, "demo_1/host_frame.border", "RU") is not None


@pytest.mark.ffmpeg
def test_real_pipeline_renders_a_transition_clip() -> None:
    preview = previews.render(_resolved("host_transition.iris"), "EN", AMY)
    assert preview.video is not None
    assert preview.video[4:8] == b"ftyp"  # an MP4


def test_every_per_viewer_cache_key_fits_the_column() -> None:
    """`<item id>@<player id>#nani` must fit item_previews.item_id; a Telegram id is at most
    20 characters with its sign."""
    longest = max(
        len(item_id)
        for item_id, resolved in registry.all_items().items()
        if resolved.item.slot is CosmeticSlot.WIN_EFFECT
    )
    costume = "a" * 16  # looks.COSTUME_KEY's longest key
    key = previews.cache_key(_resolved("win_effect.confetti"), -(10**19), costume)
    assert key is not None
    assert longest + len("@") + 20 + len(previews.WITH_NANI) + len("@") + len(costume) <= (
        ITEM_ID_LENGTH
    )
    assert len(key) <= ITEM_ID_LENGTH


@pytest.mark.parametrize(("costume", "suffix"), [(None, "#nani"), ("kitsune", "#nani@kitsune")])
def test_cache_key_of_a_nani_slot_carries_the_costume(costume, suffix) -> None:
    assert previews.cache_key(_resolved("host_frame.border"), 7, costume) == (
        "demo_1/host_frame.border" + suffix
    )


def test_a_costume_change_gives_a_new_key() -> None:
    frame = _resolved("host_frame.border")
    assert previews.cache_key(frame, 1, "kitsune") != previews.cache_key(frame, 1, "skeleton")
    assert previews.cache_key(frame, 1, "kitsune") != previews.cache_key(frame, 1)


def test_a_text_slot_key_ignores_the_costume() -> None:
    flair = _resolved("name_flair.blossom")
    assert previews.cache_key(flair, 1, "kitsune") == previews.cache_key(flair, 1)


@pytest.fixture
def costume_item(costume_art, monkeypatch) -> Iterator[registry.ResolvedItem]:
    hat = Item(
        "mascot_costume.hat",
        CosmeticSlot.MASCOT_COSTUME,
        costume_art,
        MappingProxyType({"EN": "Hat", "RU": "Hat"}),
    )
    demo = registry.all_runs()["demo_1"]
    runs = {"demo_1": replace(demo, items=(*demo.items, hat))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield _resolved("mascot_costume.hat")
    registry.all_items.cache_clear()


def test_a_costume_item_is_kept_per_language(costume_item) -> None:
    key_en = previews.cache_key(costume_item, 7, "kitsune", "EN")
    assert key_en == "demo_1/mascot_costume.hat#en"
    assert key_en != previews.cache_key(costume_item, 7, "kitsune", "RU")


def test_other_slot_keys_ignore_the_language() -> None:
    for key in ("host_frame.border", "win_effect.confetti", "name_flair.blossom"):
        assert previews.cache_key(_resolved(key), 7, None, "RU") == previews.cache_key(
            _resolved(key), 7
        )


def test_the_longest_costume_key_fits_the_column(costume_item) -> None:
    key = previews.cache_key(costume_item, 7, None, "RU")
    assert key is not None
    assert len(key) <= ITEM_ID_LENGTH


def _sheet(costume_item, lang: str = "EN") -> Image.Image:
    preview = previews.render(costume_item, lang, AMY)
    assert preview.kind is PreviewKind.PHOTO
    assert preview.image is not None
    return Image.open(BytesIO(preview.image)).convert("RGB")


def _cell_origin(index: int) -> tuple[int, int]:
    col, row = index % previews.SHEET_COLUMNS, index // previews.SHEET_COLUMNS
    cell_w, cell_h = previews.SHEET_CELL
    return (
        previews.SHEET_MARGIN + col * (cell_w + previews.SHEET_GAP),
        previews.SHEET_MARGIN + row * (cell_h + previews.SHEET_GAP),
    )


def test_the_sheet_has_the_designed_size(costume_item) -> None:
    assert _sheet(costume_item).size == (812, 634)


def test_each_cell_shows_its_pose(costume_item) -> None:
    sheet = _sheet(costume_item)
    cell_w, cell_h = previews.SHEET_CELL
    for index in range(len(Pose)):
        x, y = _cell_origin(index)
        bottom = y + cell_h - previews.SHEET_LABEL_HEIGHT - 4
        assert sheet.getpixel((x + cell_w // 2, bottom - 20)) == ORANGE[:3]


def test_each_cell_has_a_pill_with_white_text(costume_item) -> None:
    sheet = _sheet(costume_item)
    cell_w, cell_h = previews.SHEET_CELL
    for index in range(len(Pose)):
        x, y = _cell_origin(index)
        top = y + cell_h - previews.SHEET_LABEL_HEIGHT + 4
        box = sheet.crop((x, top, x + cell_w, top + 32))
        assert box.convert("L").point(lambda v: 255 if v > 240 else 0).getbbox() is not None


def test_the_sheet_names_the_poses_in_the_viewers_language(costume_item) -> None:
    en, ru = _sheet(costume_item, "EN"), _sheet(costume_item, "RU")
    assert en.size == ru.size
    assert en.tobytes() != ru.tobytes()


def test_the_sheet_background_is_the_night_image(costume_item) -> None:
    sheet = _sheet(costume_item)
    with Image.open(previews.SHEET_BACKGROUND) as source:
        fitted = ImageOps.fit(source.convert("RGB"), sheet.size, Image.Resampling.LANCZOS)
    assert sheet.getpixel((3, 3)) == fitted.getpixel((3, 3))


def test_a_frame_preview_wears_the_viewers_costume(costume_art) -> None:
    plain = previews.render(_resolved("host_frame.border"), "EN", AMY).image
    worn = previews.render(
        _resolved("host_frame.border"), "EN", replace(AMY, costume=costume_art)
    ).image
    assert plain is not None
    assert worn is not None
    with Image.open(BytesIO(worn)) as image:
        corner = image.convert("RGB").getpixel((image.width - 40, image.height - 40))
    assert corner == ORANGE[:3]
    assert plain != worn


def test_clip_job_carries_the_viewers_costume() -> None:
    job = previews.clip_job(_resolved("host_transition.iris"), replace(AMY, costume="test_hat"))
    assert job is not None
    assert job.args()[4].nani.costume == "test_hat"
    plain = previews.clip_job(_resolved("win_effect.confetti"), AMY)
    assert plain is not None
    assert plain.args()[4].nani.costume is None
