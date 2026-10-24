"""A season's teaser/start/end card (seasons spec §6): the run's key visual,
unshaded, with a ribbon tag, the season name in a two-colour gradient (the
run's palette; Sakura's is pink-to-gold),
the run's stamp tilted like a seal and the dates in a frosted pill. Pure
Pillow, no Telegram; the caller localizes every string."""

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

from loguru import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from nani_pix_bot.seasons.mechanics import DEFAULT_PALETTE, Palette
from nani_pix_bot.services.cards.icons import ICON_DIR
from nani_pix_bot.services.cards.render import (
    _BACKGROUND,
    _HEIGHT,
    _MARGIN,
    _WIDTH,
    CARD_SIZE,
    _cover,
    _fit,
    _png,
    font,
)

CALENDAR_ICON = ICON_DIR / "calendar.png"
_INK = (70, 40, 60)  # the date's colour on the frosted pill
# The ribbon tag carrying the headline, top centre.
_RIBBON_TOP = 34
_RIBBON_HEIGHT = 58
_RIBBON_PAD = 90  # ribbon width over the text's
_RIBBON_NOTCH = 18
_RIBBON_ALPHA = 235
_RIBBON_SHINE_INSET = 24
_RIBBON_SIZE = 32
# The season name: a horizontal gradient fill, white outline, a faint shine on its upper half.
_TITLE_SIZES = (100, 88, 76, 64)
_TITLE_STROKE = 7
_TITLE_SHINE = 5  # the shine is the glyph mask divided by this
_TITLE_Y = _HEIGHT / 2 + 6
_TITLE_RIGHT = _WIDTH - 50
# The title is centred right of the stamp seal, which ends at about this x.
_STAMP_RIGHT = 330
_STAMP_TITLE_GAP = 40
# The stamp seal: the run's stamp art, tilted, on the left.
_STAMP_SIZE = 250
_STAMP_TILT = 14
_STAMP_X = 36
_STAMP_DROP = 4  # below the vertical centre
# The soft drop shadow under the stamp, the title and the ribbon.
_SHADOW_OFFSET = (4, 6)
_SHADOW_BLUR = 6
_SHADOW_ALPHA = 0.55
# The frosted date pill, bottom centre.
_PILL_HEIGHT = 64
_PILL_BOTTOM_GAP = 100  # the pill's top is this far above the card's bottom
_PILL_PAD = 70  # pill width over the icon and text
_PILL_BLUR = 10
_PILL_FILL = (255, 255, 255, 150)
_PILL_OUTLINE = (255, 255, 255, 210)
_PILL_SIZE = 34
_ICON = 40
_ICON_GAP = 12


@dataclass(frozen=True)
class SeasonBanner:
    headline: str
    title: str
    subtitle: str
    stamp: bytes | None = None  # the run's stamp art (PNG); None draws no seal
    palette: Palette = DEFAULT_PALETTE  # the run's ribbon and title colours


def _layer() -> Image.Image:
    return Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))


def _shadow(layer: Image.Image) -> Image.Image:
    """`layer`'s silhouette in black, fainter, moved down-right and blurred."""
    silhouette = _layer()
    silhouette.putalpha(layer.getchannel("A").point(lambda v: int(v * _SHADOW_ALPHA)))
    moved = _layer()
    moved.paste(silhouette, _SHADOW_OFFSET)
    return moved.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR))


def _ribbon_layer(text: str, color: tuple[int, int, int]) -> Image.Image:
    """A ribbon in `color` with a notch at both ends and a faint highlight line, the
    headline in white bold on it."""
    ribbon_font = font(_RIBBON_SIZE, bold=True)
    layer = _layer()
    draw = ImageDraw.Draw(layer)
    text = _fit(draw, text, ribbon_font, _WIDTH - 2 * _MARGIN - _RIBBON_PAD)
    width = int(draw.textlength(text, font=ribbon_font) + _RIBBON_PAD)
    left, top = (_WIDTH - width) // 2, _RIBBON_TOP
    right, bottom = left + width, top + _RIBBON_HEIGHT
    middle = (top + bottom) / 2
    draw.polygon(
        [
            (left, top),
            (right, top),
            (right - _RIBBON_NOTCH, middle),
            (right, bottom),
            (left, bottom),
            (left + _RIBBON_NOTCH, middle),
        ],
        fill=(*color, _RIBBON_ALPHA),
    )
    shine = (left + _RIBBON_SHINE_INSET, top + 6), (right - _RIBBON_SHINE_INSET, top + 6)
    draw.line(shine, fill=(255, 255, 255, 90), width=2)
    draw.text((_WIDTH / 2, middle), text, font=ribbon_font, fill=(255, 255, 255), anchor="mm")
    return layer


def _title_font(
    draw: ImageDraw.ImageDraw, text: str, width: int
) -> tuple[ImageFont.FreeTypeFont, str]:
    """The biggest title size whose outlined text fits `width`; the smallest one is cut."""
    for size in _TITLE_SIZES:
        title_font = font(size, bold=True)
        if draw.textlength(text, font=title_font) + 2 * _TITLE_STROKE <= width:
            return title_font, text
    return title_font, _fit(draw, text, title_font, width - 2 * _TITLE_STROKE)


@lru_cache(maxsize=8)
def _gradient_row(left: int, right: int, palette: Palette) -> Image.Image:
    """One pixel row of _gradient: cached, as it is tiny (a whole card is not)."""
    row = Image.new("RGBA", (_WIDTH, 1))
    for x in range(_WIDTH):
        t = min(1.0, max(0.0, (x - left) / max(1, right - left)))
        color = tuple(
            round(a + (b - a) * t)
            for a, b in zip(palette.title_from, palette.title_to, strict=True)
        )
        row.putpixel((x, 0), (*color, 255))
    return row


def _gradient(left: int, right: int, palette: Palette) -> Image.Image:
    """The palette's title_from at x=left to title_to at x=right, flat beyond, card-wide."""
    return _gradient_row(left, right, palette).resize(CARD_SIZE, Image.Resampling.NEAREST)


def title_layer(text: str, *, stamped: bool, palette: Palette = DEFAULT_PALETTE) -> Image.Image:
    """The season name: centred right of the stamp seal when there is one, else on
    the card; gradient-filled, outlined in white, a faint shine on the upper half."""
    left = _STAMP_RIGHT + _STAMP_TITLE_GAP if stamped else _WIDTH - _TITLE_RIGHT
    centre = ((left + _TITLE_RIGHT) / 2, _TITLE_Y)
    glyphs = Image.new("L", CARD_SIZE, 0)
    title_font, text = _title_font(ImageDraw.Draw(glyphs), text, _TITLE_RIGHT - left)
    ImageDraw.Draw(glyphs).text(centre, text, font=title_font, fill=255, anchor="mm")
    outline = Image.new("L", CARD_SIZE, 0)
    ImageDraw.Draw(outline).text(
        centre, text, font=title_font, fill=255, anchor="mm", stroke_width=_TITLE_STROKE
    )
    box = glyphs.getbbox() or (0, 0, _WIDTH, _HEIGHT)
    white = Image.new("RGBA", CARD_SIZE, (255, 255, 255, 255))
    layer = _layer()
    layer.paste(white, (0, 0), outline)
    layer.paste(_gradient(box[0], box[2], palette), (0, 0), glyphs)
    shine = glyphs.point(lambda v: v // _TITLE_SHINE)
    ImageDraw.Draw(shine).rectangle((0, int(_TITLE_Y - 6) + 1, _WIDTH, _HEIGHT), fill=0)
    layer.paste(white, (0, 0), shine)
    return layer


def _stamp_layer(stamp: bytes) -> Image.Image | None:
    """The stamp art, tilted like a seal, on the left; None if it isn't an image."""
    try:
        art = Image.open(BytesIO(stamp)).convert("RGBA")
    except OSError:
        logger.warning("the season's stamp art isn't an image — no seal on the card")
        return None
    seal = art.resize((_STAMP_SIZE, _STAMP_SIZE), Image.Resampling.LANCZOS).rotate(
        _STAMP_TILT, resample=Image.Resampling.BICUBIC, expand=True
    )
    layer = _layer()
    layer.alpha_composite(seal, (_STAMP_X, _HEIGHT // 2 - seal.height // 2 + _STAMP_DROP))
    return layer


@lru_cache(maxsize=1)
def _calendar() -> Image.Image:
    with Image.open(CALENDAR_ICON) as icon:
        return icon.convert("RGBA").resize((_ICON, _ICON), Image.Resampling.LANCZOS)


def _date_pill(image: Image.Image, text: str) -> None:
    """The dates in a frosted pill: the picture behind it blurred, a white veil and
    outline, the calendar icon, then the text."""
    pill_font = font(_PILL_SIZE)
    draw = ImageDraw.Draw(image)
    room = _WIDTH - 2 * _MARGIN - _PILL_PAD - _ICON - _ICON_GAP
    text = _fit(draw, text, pill_font, room)
    width = int(draw.textlength(text, font=pill_font) + _PILL_PAD + _ICON + _ICON_GAP)
    left, top = (_WIDTH - width) // 2, _HEIGHT - _PILL_BOTTOM_GAP
    right, bottom = left + width, top + _PILL_HEIGHT  # exclusive, as crop takes them
    radius = _PILL_HEIGHT // 2
    mask = Image.new("L", (width, _PILL_HEIGHT), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, _PILL_HEIGHT - 1), radius=radius, fill=255
    )
    behind = image.crop((left, top, right, bottom)).filter(ImageFilter.GaussianBlur(_PILL_BLUR))
    image.paste(behind, (left, top), mask)
    frost = _layer()
    # The drawn shape takes inclusive corners: the same pixels as the mask.
    ImageDraw.Draw(frost).rounded_rectangle(
        (left, top, right - 1, bottom - 1),
        radius=radius,
        fill=_PILL_FILL,
        outline=_PILL_OUTLINE,
        width=2,
    )
    image.alpha_composite(frost)
    x = left + _PILL_PAD // 2
    image.alpha_composite(_calendar(), (x, top + (_PILL_HEIGHT - _ICON) // 2))
    x += _ICON + _ICON_GAP
    ImageDraw.Draw(image).text(
        (x, top + _PILL_HEIGHT / 2), text, font=pill_font, fill=_INK, anchor="lm"
    )


def render_season_banner(card: SeasonBanner, background: bytes | None = None) -> bytes:
    """A season's teaser/start/end card over the run's key visual (unshaded), else
    the flat card: stamp seal, season name and ribbon tag, each with a soft
    shadow, then the dates. An empty headline leaves out the ribbon and empty
    dates the pill (the guide's plain picture of a season)."""
    picture = _cover(background, CARD_SIZE) if background is not None else None
    if picture is None:
        picture = Image.new("RGB", CARD_SIZE, _BACKGROUND)
    image = picture.convert("RGBA")
    seal = _stamp_layer(card.stamp) if card.stamp is not None else None
    headline, palette = card.headline, card.palette
    ribbon = _ribbon_layer(headline, palette.ribbon) if headline else None
    for layer in (
        seal,
        title_layer(card.title, stamped=seal is not None, palette=card.palette),
        ribbon,
    ):
        if layer is not None:
            image.alpha_composite(_shadow(layer))
            image.alpha_composite(layer)
    if card.subtitle:
        _date_pill(image, card.subtitle)
    return _png(image.convert("RGB"))
