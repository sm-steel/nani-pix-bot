import math
from io import BytesIO
from typing import Any, cast

from PIL import Image

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services import cards
from nani_pix_bot.services.cards import backgrounds, render


def _card(**overrides) -> cards.UnlockCard:
    fields: dict[str, Any] = {
        "headline": "Achievement unlocked",
        "name": "Sharpshooter III",
        "description": "Games won: 10",
        "handle": "@alice",
        "reward": 200,
        "points_label": "+8 pts",
        "rarity_label": "Gold",
        "rarity": Rarity.GOLD,
        "seed": 42,
    } | overrides
    return cards.UnlockCard(**fields)


def _red_avatar() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def _open(png: bytes) -> Image.Image:
    return Image.open(BytesIO(png))


def test_unlock_card_is_a_png_of_the_card_size() -> None:
    image = _open(cards.render_unlock_card(_card(), None))
    assert image.format == "PNG"
    assert image.size == cards.CARD_SIZE


def test_rendering_is_deterministic() -> None:
    assert cards.render_unlock_card(_card(), None) == cards.render_unlock_card(_card(), None)


def test_long_cyrillic_text_renders_without_error() -> None:
    card = _card(
        name="Пиксельный магнат " * 5,
        description="Потрачено 💠 на подсказки, /sharpen и выплаченные награды: 10000 " * 3,
        handle="@очень_длинное_имя_пользователя_которое_не_влезет",
    )
    assert _open(cards.render_unlock_card(card, None)).size == cards.CARD_SIZE


def test_a_photo_avatar_replaces_the_initials_disc() -> None:
    with_photo = cards.avatar_disc(_red_avatar(), "@alice", 42, 100).convert("RGB")
    fallback = cards.avatar_disc(None, "@alice", 42, 100).convert("RGB")
    assert with_photo.getpixel((20, 50)) == (255, 0, 0)
    assert fallback.getpixel((20, 50)) != (255, 0, 0)


def test_a_broken_avatar_falls_back_to_initials() -> None:
    disc = cards.avatar_disc(b"not an image", "@alice", 42, 100)
    assert disc.size == (100, 100)


def test_podium_renders_one_to_three_entries() -> None:
    for count in (1, 2, 3):
        entries = tuple(cards.PodiumEntry(f"@p{i}", f"{10 - i} pts", seed=i) for i in range(count))
        png = cards.render_podium_card(cards.PodiumCard("Champions of October 2026", entries))
        assert _open(png).size == cards.CARD_SIZE


def _busy_background() -> bytes:
    image = Image.new("RGB", (800, 800))
    for x in range(0, 800, 4):
        for y in range(0, 800, 4):
            image.paste(((x * 7) % 256, (y * 5) % 256, 255 - (x + y) % 256), (x, y, x + 4, y + 4))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _use_backgrounds(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(backgrounds, "BACKGROUND_DIR", tmp_path)


def test_load_background_is_none_for_a_missing_or_empty_slot(monkeypatch, tmp_path) -> None:
    _use_backgrounds(monkeypatch, tmp_path)
    assert cards.load_background("unlock/gold", 1) is None
    (tmp_path / "unlock" / "gold").mkdir(parents=True)
    (tmp_path / "unlock" / "gold" / "notes.txt").write_text("x")
    assert cards.load_background("unlock/gold", 1) is None


def test_load_background_picks_deterministically_by_seed(monkeypatch, tmp_path) -> None:
    _use_backgrounds(monkeypatch, tmp_path)
    folder = tmp_path / "podium" / "week"
    folder.mkdir(parents=True)
    (folder / "b.png").write_bytes(b"B")
    (folder / "a.PNG").write_bytes(b"A")
    assert cards.load_background("podium/week", 0) == b"A"
    assert cards.load_background("podium/week", 1) == b"B"
    assert cards.load_background("podium/week", 2) == b"A"


def test_slot_names() -> None:
    assert cards.unlock_slot(Rarity.GOLD) == "unlock/gold"
    assert cards.podium_slot("month") == "podium/month"


def test_cards_render_with_a_background() -> None:
    background = _busy_background()
    assert _open(cards.render_unlock_card(_card(), None, background)).size == cards.CARD_SIZE
    podium = cards.PodiumCard("Champions", (cards.PodiumEntry("@a", "1 pt", seed=1),))
    assert _open(cards.render_podium_card(podium, background)).size == cards.CARD_SIZE


def test_a_broken_background_falls_back_to_the_flat_card() -> None:
    flat = cards.render_unlock_card(_card(), None)
    assert cards.render_unlock_card(_card(), None, b"not an image") == flat
    podium = cards.PodiumCard("Champions", (cards.PodiumEntry("@a", "1 pt", seed=1),))
    assert cards.render_podium_card(podium, b"nope") == cards.render_podium_card(podium)


def test_a_background_shows_but_stays_dark_behind_the_text() -> None:
    flat = _open(cards.render_unlock_card(_card(), None)).convert("RGB")
    shaded = _open(cards.render_unlock_card(_card(), None, _busy_background())).convert("RGB")
    area = (760, 300, 1100, 460)  # empty text area: only background shows
    assert flat.crop(area).tobytes() != shaded.crop(area).tobytes()
    luminance = shaded.crop(area).convert("L")
    assert (
        luminance.point(lambda v: 255 if v >= 100 else 0).getbbox() is None
    )  # ~88% veil over a maximal-bright picture


def test_initials_use_letters_only() -> None:
    assert render._initials("@kurogane_42") == "K"
    assert render._initials("@Sakura_chan") == "SC"
    assert render._initials("@alice") == "A"
    assert render._initials("12345") == "?"
    assert render._initials("@__--") == "?"
    assert render._initials("Карина Иванова") == "КИ"


def _pixel(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    return cast(tuple[int, ...], image.getpixel(xy))


def _badge(size: int = 100, color=(240, 190, 40)) -> Image.Image:
    face = cards.avatar_disc(_red_avatar(), "@a", 1, size * render._SS)
    return render._badge(face, color)


def _ring_radius(size: int) -> float:
    return size / 2 + render._INNER_RING


def test_the_badge_edges_are_anti_aliased() -> None:
    size = 100
    badge = _badge(size)
    centre = badge.width / 2
    radius = size / 2  # where the red photo meets the gold inner ring
    seen = set()
    for step in range(360):
        angle = math.radians(step)
        for offset in (-0.5, 0.0, 0.5):
            x = centre + (radius + offset) * math.cos(angle)
            y = centre + (radius + offset) * math.sin(angle)
            seen.add(_pixel(badge, (round(x), round(y)))[1])
    # green channel: red photo = 0, gold ring = 190; anything between is a blended edge pixel
    assert any(20 < g < 170 for g in seen)


def test_the_glow_brightens_just_outside_the_outer_ring() -> None:
    size = 100
    badge = _badge(size)
    flat = Image.new("RGBA", badge.size, (*render._BACKGROUND, 255))
    on_flat = Image.alpha_composite(flat, badge).convert("RGB")
    probe = (round(badge.width / 2 + size / 2 + render._RING_TOTAL + 4), badge.height // 2)
    assert sum(_pixel(on_flat, probe)) > sum(render._BACKGROUND) + 10
    assert _pixel(on_flat, (0, 0)) == render._BACKGROUND  # the glow fades out before the edge


def test_the_outer_ring_is_lighter_top_left_and_darker_bottom_right() -> None:
    size = 100
    badge = _badge(size).convert("RGB")
    centre, ring = badge.width // 2, size / 2 + render._INNER_RING + render._OUTER_RING / 2
    offset = round(ring / math.sqrt(2))
    top_left = _pixel(badge, (centre - offset, centre - offset))
    bottom_right = _pixel(badge, (centre + offset, centre + offset))
    assert sum(top_left) > sum(bottom_right)
