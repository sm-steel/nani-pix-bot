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


def test_the_blocked_attacker_is_faded() -> None:
    attack = Image.new("RGBA", (200, 300), (255, 0, 0, 255))
    image = _open(moments.blocked_duel_card(Mood.CHARM, DUEL, attack, ART, "Title"))

    # the art sits at (230, 300); at 55% the red is blended into the background
    red = _px(image, (200, 250))[0]
    assert 100 < red < 235


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
