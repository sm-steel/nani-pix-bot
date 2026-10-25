from io import BytesIO

import pytest
from PIL import Image, ImageOps

from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.services.cosmetics import consumable_card

KINDS = list(ConsumableKind)
SIZE = consumable_card.CARD_SIZE
LABEL_TOP = SIZE - 48 - 64 + 6


def _card(kind: ConsumableKind, lang: str = "EN") -> Image.Image:
    return Image.open(BytesIO(consumable_card.render(kind, lang))).convert("RGB")


def test_the_cache_key_names_the_kind_and_the_language() -> None:
    assert consumable_card.cache_key(ConsumableKind.CLUE_TICKET, "RU") == "consumable/clue_token#RU"
    assert consumable_card.cache_key(ConsumableKind.OFUDA, "EN") == "consumable/ofuda#EN"


def test_the_name_has_no_emoji() -> None:
    assert consumable_card.name(ConsumableKind.OFUDA, "EN") == "Ofuda"
    assert consumable_card.name(ConsumableKind.OMAMORI, "RU") == "Омамори"
    assert consumable_card.name(ConsumableKind.CLUE_TICKET, "EN") == "Clue token"


@pytest.mark.parametrize("kind", KINDS)
def test_a_card_is_a_square_png(kind) -> None:
    image = Image.open(BytesIO(consumable_card.render(kind, "EN")))
    assert (image.format, image.size) == ("PNG", (SIZE, SIZE))


@pytest.mark.parametrize("kind", KINDS)
def test_the_corner_is_the_fitted_background(kind) -> None:
    with Image.open(consumable_card.BACKGROUND) as source:
        fitted = ImageOps.fit(source.convert("RGB"), (SIZE, SIZE), Image.Resampling.LANCZOS)
    assert _card(kind).getpixel((3, 3)) == fitted.getpixel((3, 3))


@pytest.mark.parametrize("kind", KINDS)
def test_the_label_pill_has_white_text(kind) -> None:
    box = _card(kind).crop((0, LABEL_TOP, SIZE, LABEL_TOP + 44))
    assert box.convert("L").point(lambda v: 255 if v > 240 else 0).getbbox() is not None


def test_the_name_is_in_the_viewers_language() -> None:
    en, ru = _card(ConsumableKind.OFUDA, "EN"), _card(ConsumableKind.OFUDA, "RU")
    assert en.tobytes() != ru.tobytes()


@pytest.mark.parametrize("kind", KINDS)
def test_the_art_area_shows_the_item(kind) -> None:
    from nani_pix_bot.services.cosmetics import card_style

    bare = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    card_style.draw_card(bare, consumable_card.CARD_BOX, consumable_card.CARD_RADIUS)
    area = (150, 150, 490, 450)
    with Image.open(consumable_card.BACKGROUND) as source:
        base = ImageOps.fit(source.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS)
    base.alpha_composite(bare)
    assert _card(kind).crop(area).tobytes() != base.convert("RGB").crop(area).tobytes()


@pytest.mark.parametrize("kind", KINDS)
def test_the_art_is_trimmed_and_fits_the_art_size(kind) -> None:
    art = consumable_card.art(kind)
    assert art.mode == "RGBA"
    assert max(art.size) == consumable_card.ART_SIZE


def test_a_missing_art_file_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(consumable_card, "ICON_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        consumable_card.art(ConsumableKind.OFUDA)
