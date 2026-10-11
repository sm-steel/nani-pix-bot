from io import BytesIO

from PIL import Image

from nani_pix_bot.services import cards
from nani_pix_bot.services.cards import profile_card

CARD = cards.ProfileCard(
    name="@alice",
    title="Demo Star",
    stamps=(),
    stats=(cards.StatEntry("medal", "Seasons: 3"), cards.StatEntry("gift", "Cosmetics: 4")),
    seed=1,
)


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _render(background: Image.Image) -> Image.Image:
    png = cards.render_profile_card(CARD, None, _png(background))
    return Image.open(BytesIO(png)).convert("RGB")


def _banner() -> Image.Image:
    across = Image.linear_gradient("L").rotate(90).resize(cards.CARD_SIZE)
    down = Image.linear_gradient("L").resize(cards.CARD_SIZE)
    return Image.merge("RGB", (across, down, Image.new("L", cards.CARD_SIZE, 200)))


def test_the_banner_shows_unshaded() -> None:
    """Right of the text column, away from the glass grid: the banner itself."""
    banner = _banner()
    out = _render(banner)
    for xy in ((1170, 300), (1170, 500), (800, 450)):
        assert out.getpixel(xy) == banner.getpixel(xy)


def test_the_stats_are_chips() -> None:
    grey = (200, 200, 200)
    out = _render(Image.new("RGB", cards.CARD_SIZE, grey))
    x = profile_card._PROFILE_TEXT_X + profile_card._CHIP_PAD * 2 + profile_card._STAT_ICON - 6
    pixel = out.getpixel((x, profile_card._STATS_Y))
    assert isinstance(pixel, tuple)
    fill, alpha = profile_card._CHIP_FILL[:3], profile_card._CHIP_FILL[3] / 255
    expected = tuple(round(g + (f - g) * alpha) for g, f in zip(grey, fill, strict=True))
    assert all(abs(a - b) <= 2 for a, b in zip(pixel, expected, strict=True))


def test_the_name_is_outlined_in_black() -> None:
    out = _render(Image.new("RGB", cards.CARD_SIZE, (255, 255, 255)))
    x = profile_card._PROFILE_TEXT_X
    name = out.crop((x - 4, 96, x + 300, 190))
    dark = name.convert("L").point(lambda v: 255 if v < 30 else 0)
    assert dark.getbbox() is not None


def test_a_broken_banner_falls_back_to_the_gradient() -> None:
    plain = cards.render_profile_card(CARD, None, None)
    assert cards.render_profile_card(CARD, None, b"not an image") == plain


def _has_black_outline(out: Image.Image, box: tuple[int, int, int, int]) -> bool:
    dark = out.crop(box).convert("L").point(lambda v: 255 if v < 30 else 0)
    return dark.getbbox() is not None


def test_the_title_is_outlined_in_black() -> None:
    out = _render(Image.new("RGB", cards.CARD_SIZE, (255, 255, 255)))
    x = profile_card._PROFILE_TEXT_X
    assert _has_black_outline(out, (x - 4, 196, x + 300, 246))


def test_the_stamp_label_is_outlined_in_black() -> None:
    card = cards.ProfileCard(
        name="@a", title=None, stamps=(cards.StampEntry("Sakura"),), stats=(), seed=1
    )
    png = cards.render_profile_card(card, None, _png(Image.new("RGB", cards.CARD_SIZE, "white")))
    out = Image.open(BytesIO(png)).convert("RGB")
    x = profile_card._PROFILE_TEXT_X
    label_y = profile_card._STAMP_Y + profile_card._STAMP_SIZE + 22
    assert _has_black_outline(out, (x - 4, label_y - 14, x + 120, label_y + 14))
