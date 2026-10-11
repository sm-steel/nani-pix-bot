from nani_pix_bot.commands.shop.keyboards import shop_keyboard
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services.clues.shop import Offer


def _labels(offer: Offer, lang: str) -> list[str]:
    return [b.text for row in shop_keyboard([offer], 1, lang).inline_keyboard for b in row]


def test_a_token_offer_shows_the_token_instead_of_the_price() -> None:
    offer = Offer(ClueKind.FIRST_LETTER, 100, True, token=True)

    label = _labels(offer, "en")[0]

    assert "🎟" in label
    assert "100" not in label


def test_a_token_offer_is_translated() -> None:
    offer = Offer(ClueKind.FIRST_LETTER, 100, True, token=True)

    assert "жетон" in _labels(offer, "ru")[0]
