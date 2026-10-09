import colorsys
import math
from io import BytesIO
from itertools import pairwise
from typing import Any, cast

from PIL import Image, ImageChops

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services import cards
from nani_pix_bot.services.cards import backgrounds, icons, render


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
        entries = tuple(
            cards.PodiumEntry(f"@p{i}", f"{10 - i} pts", seed=i, rank=i + 1) for i in range(count)
        )
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
    podium = cards.PodiumCard("Champions", (cards.PodiumEntry("@a", "1 pt", seed=1, rank=1),))
    assert _open(cards.render_podium_card(podium, background)).size == cards.CARD_SIZE


def test_a_broken_background_falls_back_to_the_flat_card() -> None:
    flat = cards.render_unlock_card(_card(), None)
    assert cards.render_unlock_card(_card(), None, b"not an image") == flat
    podium = cards.PodiumCard("Champions", (cards.PodiumEntry("@a", "1 pt", seed=1, rank=1),))
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


def _badge(size: int = 100, rarity: Rarity = Rarity.GOLD) -> Image.Image:
    face = cards.avatar_disc(_red_avatar(), "@a", 1, size * render.SUPERSAMPLE)
    return render.badge_ring(face, render.RARITY_COLORS[rarity], rarity)


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


def _outer_ring_samples(badge: Image.Image, size: int) -> list[tuple[int, ...]]:
    centre = badge.width / 2
    radius = size / 2 + render._INNER_RING + render._OUTER_RING / 2
    rgb = badge.convert("RGB")
    points = []
    for step in range(12):
        angle = math.radians(step * 30 + 7)
        points.append(
            (round(centre + radius * math.cos(angle)), round(centre + radius * math.sin(angle)))
        )
    return [_pixel(rgb, point) for point in points]


def _distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def test_the_outer_ring_sweeps_through_colours_unlike_the_inner_ring() -> None:
    for rarity in Rarity:
        badge = _badge(100, rarity)
        inner = render.RARITY_COLORS[rarity]
        samples = _outer_ring_samples(badge, 100)
        assert len(set(samples)) >= 8  # it varies around the circle
        assert all(_distance(s, inner) > 12 for s in samples)  # and never matches the inner ring


def test_the_outer_ring_has_a_different_average_hue_than_the_inner_ring() -> None:
    for rarity in Rarity:
        samples = _outer_ring_samples(_badge(100, rarity), 100)
        mean = tuple(sum(channel) / len(samples) / 255 for channel in zip(*samples, strict=True))
        inner = tuple(c / 255 for c in render.RARITY_COLORS[rarity])
        ring_hue, inner_hue = colorsys.rgb_to_hsv(*mean)[0], colorsys.rgb_to_hsv(*inner)[0]
        gap = abs(ring_hue - inner_hue)
        assert min(gap, 1 - gap) > 0.01, rarity


def _blank(size: int) -> Image.Image:
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def _difference_box(a: bytes, b: bytes) -> tuple[int, int, int, int] | None:
    return ImageChops.difference(_open(a).convert("RGB"), _open(b).convert("RGB")).getbbox()


def test_the_trophy_is_drawn_and_anti_aliased() -> None:
    trophy = icons.trophy(32)
    assert trophy.size == (32, 32)
    alphas = {_pixel(trophy, (x, y))[3] for x in range(32) for y in range(32)}
    assert 255 in alphas
    assert 0 in alphas
    assert any(0 < a < 255 for a in alphas)


def test_the_star_has_a_soft_halo_around_a_bright_core() -> None:
    star = icons.star(44)
    centre = _pixel(star, (22, 22))
    assert centre[3] == 255
    assert min(centre[:3]) > 200  # white-gold core
    halo = [_pixel(star, (x, 22))[3] for x in range(44)]
    assert any(0 < a < 255 for a in halo)  # the glow fades out rather than ending hard


def test_the_unlock_card_draws_the_trophy_before_the_points(monkeypatch) -> None:
    with_icon = cards.render_unlock_card(_card(points_label="+8"), None)
    monkeypatch.setattr(render, "trophy", _blank)
    without = cards.render_unlock_card(_card(points_label="+8"), None)
    box = _difference_box(with_icon, without)
    assert box is not None
    assert box[1] > 480  # in the footer line, nowhere else
    assert box[3] < 600


def test_the_podium_draws_a_star_before_each_score(monkeypatch) -> None:
    entries = tuple(
        cards.PodiumEntry(f"@p{i}", f"{9 - i} · {i} wins", seed=i, rank=i + 1) for i in range(3)
    )
    podium = cards.PodiumCard("Champions", entries)
    with_icon = cards.render_podium_card(podium)
    monkeypatch.setattr(render, "star", _blank)
    without = cards.render_podium_card(podium)
    box = _difference_box(with_icon, without)
    assert box is not None
    assert box[1] > 400  # all three stars sit in the score line under the avatars


def _has_blend(image: Image.Image, a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """Whether any pixel lies strictly between colours `a` and `b` on the channel
    where they differ most: a blended edge pixel, not either flat colour."""
    channel = max(range(3), key=lambda c: abs(a[c] - b[c]))
    low, high = sorted((a[channel], b[channel]))
    histogram = image.convert("RGB").getchannel(channel).histogram()
    return any(histogram[v] for v in range(low + 1, high))


def test_the_footer_medal_edge_blends_into_the_card() -> None:
    color = render.RARITY_COLORS[Rarity.GOLD]
    card = cards.render_unlock_card(_card(rarity=Rarity.GOLD), None)
    left = 2 * render._MARGIN + render._AVATAR + 2 * render._RING_TOTAL
    footer = _open(card).crop((left, 500, left + 40, 580))  # just the medal, no text
    assert _has_blend(footer, color, render._BACKGROUND)


def test_the_medal_and_diamond_sprites_are_anti_aliased() -> None:
    for sprite in (icons.medal(36, (255, 0, 0), (0, 0, 0)), icons.diamond(32, (0, 0, 255))):
        alphas = {
            _pixel(sprite, (x, y))[3] for x in range(sprite.width) for y in range(sprite.height)
        }
        assert {0, 255} <= alphas
        assert any(0 < a < 255 for a in alphas)


def test_the_footer_diamond_edge_blends_into_the_card() -> None:
    card = cards.render_unlock_card(_card(), None)
    assert _has_blend(_open(card).crop((0, 500, 1200, 600)), render._CURRENCY, render._BACKGROUND)


def _badges_apart(slots: tuple[render.PodiumSlot, ...]) -> bool:
    """No two badges (avatar plus both rings) touch on the card."""
    ordered = sorted(slots, key=lambda s: s.x)
    return all(
        b.x - a.x >= (a.size + b.size) / 2 + 2 * render._RING_TOTAL for a, b in pairwise(ordered)
    )


def test_the_classic_podium_keeps_its_slots() -> None:
    # The layout every plain 1/2/3 podium has always had: draw code is
    # unchanged, so these slots keep those cards pixel-identical.
    classic = (
        render.PodiumSlot(entry=0, place=0, x=600, size=230, name_width=280),
        render.PodiumSlot(entry=1, place=1, x=300, size=170, name_width=280),
        render.PodiumSlot(entry=2, place=2, x=900, size=170, name_width=280),
    )
    assert render.podium_slots((1, 2, 3)) == classic
    assert render.podium_slots((1, 2)) == classic[:2]
    assert render.podium_slots((1,)) == classic[:1]


def test_co_champions_are_all_large_and_spread_out() -> None:
    two = render.podium_slots((1, 1))
    assert [(s.x, s.size, s.place) for s in two] == [(400, 230, 0), (800, 230, 0)]
    three = render.podium_slots((1, 1, 1))
    assert sorted(s.x for s in three) == [300, 600, 900]
    assert {s.size for s in three} == {230}
    assert _badges_apart(three)


def test_a_shared_rank_podium_shows_everyone_without_overlap() -> None:
    for ranks in ((1, 1, 3), (1, 2, 2, 2), (1, 2, 2), (1, 1, 1, 1)):
        slots = render.podium_slots(ranks)
        assert sorted(s.entry for s in slots) == list(range(len(ranks)))
        assert _badges_apart(slots), ranks
        for slot in slots:
            assert slot.place == ranks[slot.entry] - 1
    wide = render.podium_slots((1, 2, 2, 2))
    assert sorted(s.x for s in wide) == [240, 480, 720, 960]
    champion = next(s for s in wide if s.place == 0)
    assert all(champion.size >= s.size for s in wide)


def test_a_fourth_podium_entry_is_drawn() -> None:
    entries = [
        cards.PodiumEntry(f"@p{i}", "5 pts", seed=i, rank=r) for i, r in enumerate((1, 2, 2, 2))
    ]
    four = _open(cards.render_podium_card(cards.PodiumCard("Champions", tuple(entries))))
    three = _open(cards.render_podium_card(cards.PodiumCard("Champions", tuple(entries[:3]))))
    assert four.size == cards.CARD_SIZE
    assert ImageChops.difference(four.convert("RGB"), three.convert("RGB")).getbbox() is not None


def test_the_card_draws_at_most_four_keeping_the_champions() -> None:
    six = render.podium_slots((1, 2, 2, 2, 2, 2))
    assert len(six) == render.PODIUM_CARD_MAX == 4
    assert [s.entry for s in six] == [0, 1, 2, 3]  # champion, then stored order
    late_champion = render.podium_slots((2, 2, 2, 2, 1, 3))
    assert 4 in {s.entry for s in late_champion}
    assert len(late_champion) == 4
    assert 5 not in {s.entry for s in late_champion}  # lowest ranks win the seats
    many = render.podium_slots((1,) * 7)
    assert [s.entry for s in many] == [0, 1, 2, 3]


def test_a_crowded_podium_never_shrinks_below_the_four_entry_sizes() -> None:
    floor = {s.place == 0: s.size for s in render.podium_slots((1, 2, 2, 2))}
    for ranks in ((1, 2, 2, 2, 2, 2), (1, 1, 1, 1, 1), (1, 2, 3, 3, 3, 3, 3)):
        slots = render.podium_slots(ranks)
        assert _badges_apart(slots)
        for slot in slots:
            assert slot.size >= floor[slot.place == 0]


def test_a_six_entry_podium_renders() -> None:
    entries = tuple(
        cards.PodiumEntry(f"@p{i}", "5 pts", seed=i, rank=r)
        for i, r in enumerate((1, 2, 2, 2, 2, 2))
    )
    assert _open(cards.render_podium_card(cards.PodiumCard("Champions", entries))).size == (
        cards.CARD_SIZE
    )
