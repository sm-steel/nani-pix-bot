from io import BytesIO

import pytest
from PIL import Image

from nani_pix_bot.services import cards
from nani_pix_bot.services.cards import render, season_card

W, H = cards.CARD_SIZE
CARD = cards.SeasonBanner(
    headline="Season started",
    title="Sakura Season",
    subtitle="01.04 00:00 \u2013 10.04 00:00 (Europe/Moscow)",
)


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _key_visual() -> Image.Image:
    """A picture with a different colour at every corner."""
    across = Image.linear_gradient("L").rotate(90).resize(cards.CARD_SIZE)
    down = Image.linear_gradient("L").resize(cards.CARD_SIZE)
    return Image.merge("RGB", (across, down, Image.new("L", cards.CARD_SIZE, 90)))


def _stamp() -> bytes:
    return _png(Image.new("RGBA", (512, 512), (230, 80, 120, 255)))


def _draw(stamp: bytes | None, key_visual: bytes | None) -> Image.Image:
    card = cards.SeasonBanner(CARD.headline, CARD.title, CARD.subtitle, stamp=stamp)
    return Image.open(BytesIO(cards.render_season_banner(card, key_visual))).convert("RGB")


@pytest.mark.parametrize("stamp", [None, _stamp()])
@pytest.mark.parametrize("key_visual", [None, _png(_key_visual())])
def test_the_card_renders_with_and_without_art(stamp, key_visual) -> None:
    assert _draw(stamp, key_visual).size == cards.CARD_SIZE


@pytest.mark.parametrize("corner", [(4, 4), (W - 5, 4), (4, H - 5), (W - 5, H - 5)])
def test_the_key_visual_shows_unshaded(corner) -> None:
    picture = _key_visual()
    out = _draw(_stamp(), _png(picture))
    assert out.getpixel(corner) == picture.getpixel(corner)


def test_without_a_key_visual_the_card_is_flat() -> None:
    out = _draw(None, None)
    assert out.getpixel((4, 4)) == render._BACKGROUND


def test_with_a_stamp_the_title_sits_right_of_it() -> None:
    layer = season_card.title_layer(CARD.title, stamped=True)
    box = layer.getbbox()
    assert box is not None
    assert box[0] > 370


def test_without_a_stamp_the_title_is_centred() -> None:
    box = season_card.title_layer(CARD.title, stamped=False).getbbox()
    assert box is not None
    assert abs((box[0] + box[2]) / 2 - W / 2) <= 2


def test_the_stamp_seal_is_drawn_left_of_the_title() -> None:
    plain = _draw(None, _png(_key_visual()))
    stamped = _draw(_stamp(), _png(_key_visual()))
    assert stamped.getpixel((180, H // 2)) != plain.getpixel((180, H // 2))
    assert stamped.getpixel((180, H // 2)) == (230, 80, 120)


def test_a_long_title_gets_a_smaller_font() -> None:
    short = season_card.title_layer("Sakura", stamped=True).getbbox()
    long = season_card.title_layer("The Very Long Midsummer Neon Season", stamped=True).getbbox()
    assert short is not None
    assert long is not None
    assert long[3] - long[1] < short[3] - short[1]
    assert long[2] <= W - 50 + 8  # the stroke may reach a few pixels past the text box


def test_the_ribbon_is_pink_and_centred() -> None:
    out = _draw(None, None)
    pixel = out.getpixel((W // 2 - 130, 34 + 40))
    assert isinstance(pixel, tuple)
    assert pixel[0] > 200
    assert pixel[0] > pixel[1]


def test_the_date_pill_is_frosted_white() -> None:
    out = _draw(None, None)
    pixel = out.getpixel((W // 2, H - 100 + 4))
    assert isinstance(pixel, tuple)
    assert min(pixel) > 120


def test_the_calendar_icon_exists_and_is_listed() -> None:
    path = season_card.CALENDAR_ICON
    with Image.open(path) as icon:
        assert icon.size == (128, 128)
    readme = (path.parent / "README.md").read_text(encoding="utf-8")
    assert "`calendar.png`" in readme
    assert "U+1F5D3" in readme
