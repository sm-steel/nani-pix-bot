"""A consumable's preview card (seasons spec §4): the item's art over a generated
background, in the costume sheet's style (`card_style`), with its name in a pill under it.
Drawn per language, since the name is the viewer's."""

from functools import cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import card_style, clue_token_icon

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ICON_DIR = ASSETS / "icons"
BACKGROUND = ASSETS / "item_card_background.jpg"
CARD_SIZE = 640
_MARGIN = 48
CARD_BOX = (_MARGIN, _MARGIN, CARD_SIZE - _MARGIN, CARD_SIZE - _MARGIN)
CARD_RADIUS = 28
ART_SIZE = 400
_LABEL_AREA = 64  # the strip under the art that holds the pill
_LABEL_PILL = card_style.PillSize(height=44, radius=20, font_size=30, padding=44)


def cache_key(kind: ConsumableKind, lang: str) -> str:
    return f"consumable/{kind.value}#{lang}"


def name(kind: ConsumableKind, lang: str) -> str:
    """The item's name, without an emoji (the card font has none)."""
    return i18n.t(f"consumable.name.{kind.value}", lang)


def _trimmed(image: Image.Image) -> Image.Image:
    """`image` cropped to what is drawn, scaled so its longest side is ART_SIZE."""
    image = image.convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box is not None:
        image = image.crop(box)
    scale = ART_SIZE / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def art(kind: ConsumableKind) -> Image.Image:
    """The item's picture, trimmed to its shape, longest side ART_SIZE. Raises
    FileNotFoundError for a kind whose PNG is missing."""
    if kind is ConsumableKind.CLUE_TICKET:
        return _trimmed(clue_token_icon.ticket())
    with Image.open(ICON_DIR / f"{kind.value}.png") as source:
        return _trimmed(source)


@cache
def _background() -> Image.Image:
    """The generated picture fitted to the card, decoded once per process."""
    with Image.open(BACKGROUND) as source:
        return ImageOps.fit(
            source.convert("RGBA"), (CARD_SIZE, CARD_SIZE), Image.Resampling.LANCZOS
        )


def render(kind: ConsumableKind, lang: str) -> bytes:
    """The card as a 640x640 PNG."""
    card = _background().copy()
    card_style.draw_card(card, CARD_BOX, CARD_RADIUS)
    picture = art(kind)
    area_height = CARD_SIZE - 2 * _MARGIN - _LABEL_AREA
    card.alpha_composite(
        picture,
        ((CARD_SIZE - picture.width) // 2, _MARGIN + (area_height - picture.height) // 2),
    )
    card_style.draw_pill(
        card,
        name(kind, lang),
        _LABEL_PILL,
        (0, CARD_SIZE, CARD_SIZE - _MARGIN - _LABEL_AREA + 6),
    )
    out = BytesIO()
    card.convert("RGB").save(out, format="PNG")
    return out.getvalue()
