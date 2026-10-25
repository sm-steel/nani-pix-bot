from io import BytesIO
from typing import cast

import pytest
from PIL import Image

from nani_pix_bot.services.cards import moments
from nani_pix_bot.services.cards.moments import Duel, Face, Mood

ART = Image.new("RGBA", (200, 300), (200, 40, 40, 255))
ALICE_PHOTO = BytesIO()
Image.new("RGB", (64, 64), (20, 120, 20)).save(ALICE_PHOTO, "PNG")
ALICE = Face(ALICE_PHOTO.getvalue(), "Alice", 1)
BOB = Face(None, "Bob", 7)
DUEL = Duel(BOB, ALICE)


def _open(card: bytes) -> Image.Image:
    return Image.open(BytesIO(card)).convert("RGB")


def _px(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], image.getpixel(xy))


def _near(pixel: tuple[int, ...], colour: tuple[int, int, int], tolerance: int = 40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def _whites(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    crop = image.crop(box)
    return sum(1 for p in crop.get_flattened_data() if min(cast(tuple[int, ...], p)) > 240)


@pytest.mark.parametrize("mood", list(Mood))
def test_every_card_is_1200_by_800(mood: Mood) -> None:
    assert _open(moments.charm_card(mood, ALICE, ART, "Title")).size == (1200, 800)
    assert _open(moments.duel_card(mood, DUEL, ART, "Title")).size == (1200, 800)
    blocked = moments.duel_card(mood, DUEL, ART, "Title", blocked=True)
    assert _open(blocked).size == (1200, 800)


@pytest.mark.parametrize("mood", list(Mood))
def test_the_ring_wears_the_mood_colour(mood: Mood) -> None:
    image = _open(moments.charm_card(mood, ALICE, ART, "Title"))

    # the 340 px disc at (480, 420): the ring sits just outside its top edge
    assert _near(_px(image, (480, 420 - 176)), moments.RING[mood])


def test_the_avatar_photo_is_the_disc() -> None:
    image = _open(moments.charm_card(Mood.CHARM, ALICE, ART, "Title"))

    assert _near(_px(image, (480, 420)), (20, 120, 20), 10)


def test_without_an_avatar_the_disc_is_the_initials_colour() -> None:
    image = _open(moments.charm_card(Mood.CHARM, BOB, ART, "Title"))
    photo = _open(moments.charm_card(Mood.CHARM, ALICE, ART, "Title"))

    assert not _near(_px(image, (420, 380)), _px(photo, (420, 380)), 10)
    # the initials are drawn in light text on the seed's colour
    assert _whites(image, (400, 340, 560, 500)) > 0 or _px(image, (420, 380)) != (0, 0, 0)


def test_the_title_is_white_text_in_the_pill() -> None:
    box = (300, 30, 900, 110)
    with_title = _whites(_open(moments.charm_card(Mood.CHARM, ALICE, ART, "Hello there")), box)
    without = _whites(_open(moments.charm_card(Mood.CHARM, ALICE, ART, "")), box)

    assert with_title > without + 150


def test_a_title_with_an_emoji_still_renders() -> None:
    assert moments.charm_card(Mood.CHARM, ALICE, ART, "📜 Ofuda saved it")


def test_a_very_long_name_and_title_still_fit() -> None:
    long_face = Face(None, "N" * 200, 3)

    card = moments.charm_card(Mood.CHARM, long_face, ART, "word " * 60)

    assert _open(card).size == (1200, 800)


def test_the_blocked_duel_is_laid_out_differently() -> None:
    plain = _open(moments.duel_card(Mood.CHARM, DUEL, ART, "Title"))
    blocked = _open(moments.duel_card(Mood.CHARM, DUEL, ART, "Title", blocked=True))

    # the target disc moves to the middle in the blocked layout
    assert _px(plain, (560, 420)) != _px(blocked, (560, 420))
    assert _near(_px(blocked, (560, 420)), (20, 120, 20), 10)


def test_the_duel_target_is_on_the_right() -> None:
    image = _open(moments.duel_card(Mood.CURSE, DUEL, ART, "Title"))

    assert _near(_px(image, (900, 420)), (20, 120, 20), 10)


def _count_red(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    crop = image.crop(box)
    pixels = cast(list[tuple[int, int, int]], list(crop.get_flattened_data()))
    return sum(1 for r, g, b in pixels if r > 180 and g < 60 and b < 60)


def test_a_wide_art_stays_clear_of_the_avatar_and_inside_the_card() -> None:
    wide = Image.new("RGBA", (800, 300), (230, 0, 0, 255))
    image = _open(moments.charm_card(Mood.CHARM, ALICE, wide, "Title"))

    # the avatar's whole area (disc, ring and halo) has no art in it
    assert _count_red(image, (480 - 184, 420 - 184, 480 + 184, 420 + 184)) == 0
    # but the art is on the card, to the right, within the 360 px box (plus its tilt)
    assert _count_red(image, (640, 200, 1200, 640)) > 5000
    assert _count_red(image, (1100, 0, 1200, 800)) == 0


def test_a_wide_art_fits_its_duel_box() -> None:
    wide = Image.new("RGBA", (800, 300), (230, 0, 0, 255))
    image = _open(moments.duel_card(Mood.CURSE, DUEL, wide, "Title"))

    # 300 px wide at (560, 400): nothing beyond its box
    assert _count_red(image, (0, 0, 400, 800)) == 0
    assert _count_red(image, (720, 0, 1200, 800)) == 0
    assert _count_red(image, (410, 300, 710, 500)) > 3000


def test_the_spirit_trail_is_solid_mood_colour_between_the_rings() -> None:
    image = _open(moments.duel_card(Mood.CURSE, DUEL, ART, "Title"))
    colour = moments.RING[Mood.CURSE]

    dots = sum(1 for x in range(349, 737) if _near(_px(image, (x, 425)), colour, 30))
    assert dots > 40


def _blocked(attack: Image.Image) -> Image.Image:
    return _open(moments.blocked_duel_card(Mood.CHARM, DUEL, attack, ART, "Title"))


def test_the_blocked_hex_is_snapped_in_two() -> None:
    attack = Image.new("RGBA", (200, 300), (255, 0, 0, 255))
    snapped = _blocked(attack)
    empty = _blocked(Image.new("RGBA", (200, 300), (0, 0, 0, 0)))

    def far(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
        return max(abs(x - y) for x, y in zip(a, b, strict=True))

    # the 153 x 230 art is centred at (230, 300): its middle is the gap between the halves
    assert far(_px(snapped, (230, 300)), _px(empty, (230, 300))) < 12
    # a little way off the break, along its normal, are the two halves
    assert far(_px(snapped, (194, 316)), _px(empty, (194, 316))) > 12
    assert far(_px(snapped, (266, 284)), _px(empty, (266, 284))) > 12


def test_the_spent_hex_is_drained_and_has_no_white_line() -> None:
    attack = Image.new("RGBA", (200, 300), (255, 0, 0, 255))
    image = _blocked(attack)
    area = (100, 170, 360, 430)

    assert _count_red(image, area) == 0  # no longer the art's full red
    assert _whites(image, area) == 0  # nothing drawn over it
    empty = _blocked(Image.new("RGBA", (200, 300), (0, 0, 0, 0)))
    assert image.crop(area).tobytes() != empty.crop(area).tobytes()


def test_the_ring_edge_is_antialiased() -> None:
    image = _open(moments.charm_card(Mood.CHARM, ALICE, ART, "Title"))

    # down the ring's top: dark halo, then the ring colour; a hard edge would jump straight
    reds = [_px(image, (480, y))[0] for y in range(232, 246)]
    low, high = min(reds), max(reds)
    assert any(low + 0.2 * (high - low) < r < high - 0.2 * (high - low) for r in reds)
