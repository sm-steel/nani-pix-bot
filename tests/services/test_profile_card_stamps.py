from io import BytesIO

from PIL import Image

from nani_pix_bot.services import cards
from nani_pix_bot.services.cards import profile_card

ART_COLOR = (250, 10, 10, 255)


def _art() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (512, 512), ART_COLOR).save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_disc_centre_comes_from_the_art() -> None:
    disc = profile_card._stamp_disc(cards.StampEntry("Sakura", gold=True, art=_art()))
    centre = disc.getpixel((disc.width // 2, disc.height // 2))
    assert centre == ART_COLOR


def test_the_art_disc_is_round_and_smooth() -> None:
    disc = profile_card._stamp_disc(cards.StampEntry("Sakura", gold=False, art=_art()))
    alpha = disc.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    assert any(alpha.histogram()[1:255])


def test_the_ring_stays_gold_around_the_art() -> None:
    disc = profile_card._stamp_disc(cards.StampEntry("Sakura", gold=True, art=_art()))
    x = disc.width // 2
    pixel = disc.getpixel((x, 1))
    assert isinstance(pixel, tuple)
    red, green, blue = pixel[:3]
    assert red > green > blue


def test_non_square_art_is_centre_cropped_not_squashed() -> None:
    """A wide picture, red in its middle square and blue in the side bands: the
    face keeps only the middle, so no blue shows and the red isn't squeezed."""
    wide = Image.new("RGBA", (300, 100), (0, 0, 255, 255))
    wide.paste((255, 0, 0, 255), (100, 0, 200, 100))
    buffer = BytesIO()
    wide.save(buffer, format="PNG")
    face = profile_card._stamp_face(cards.StampEntry("Sakura", art=buffer.getvalue()), 100)
    assert face.size == (100, 100)
    for xy in ((50, 50), (12, 50), (88, 50)):
        assert face.getpixel(xy) == (255, 0, 0, 255)


def _soft_art() -> bytes:
    """Art that is half transparent, like the soft rim of a real stamp."""
    buffer = BytesIO()
    Image.new("RGBA", (512, 512), (250, 10, 10, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_half_transparent_art_keeps_its_colour_and_alpha() -> None:
    """No dark fringe: the art's soft edges aren't darkened toward black or made fainter."""
    face = profile_card._stamp_face(cards.StampEntry("Sakura", art=_soft_art()), 200)
    pixel = face.getpixel((100, 100))
    assert isinstance(pixel, tuple)
    assert all(abs(a - b) <= 2 for a, b in zip(pixel, (250, 10, 10, 128), strict=True))


def test_the_shrunk_disc_keeps_the_soft_arts_colour() -> None:
    disc = profile_card._stamp_disc(cards.StampEntry("Sakura", gold=False, art=_soft_art()))
    pixel = disc.getpixel((disc.width // 2, disc.height // 2))
    assert isinstance(pixel, tuple)
    assert all(abs(a - b) <= 2 for a, b in zip(pixel, (250, 10, 10, 128), strict=True))


def test_without_art_the_initials_disc_is_drawn() -> None:
    disc = profile_card._stamp_disc(cards.StampEntry("Sakura", gold=False))
    assert disc.getpixel((disc.width // 2, disc.height // 2)) != ART_COLOR


def test_a_card_with_art_stamps_renders() -> None:
    card = cards.ProfileCard(
        name="@a",
        title=None,
        stamps=(cards.StampEntry("Sakura", gold=True, art=_art()),),
        stats=(),
        seed=1,
    )
    png = cards.render_profile_card(card, None, None)
    assert Image.open(BytesIO(png)).size == cards.CARD_SIZE
