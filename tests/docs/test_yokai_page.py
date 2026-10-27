"""The Yōkai Night guide page states the run's own numbers, in both languages."""

from pathlib import Path

import pytest

from nani_pix_bot.seasons.definition import RewardKind, ShopOffer
from nani_pix_bot.seasons.runs.yokai_1 import RUN
from nani_pix_bot.services import i18n

DOCS = Path(__file__).resolve().parents[2] / "docs" / "site" / "src" / "content" / "docs"
PAGES = {
    "EN": DOCS / "seasons" / "yokai-night.mdx",
    "RU": DOCS / "ru" / "seasons" / "yokai-night.mdx",
}
LANGS = pytest.mark.parametrize("lang", sorted(PAGES))


def _page(lang: str) -> str:
    return PAGES[lang].read_text(encoding="utf-8")


def _rows(lang: str) -> list[str]:
    return [line for line in _page(lang).splitlines() if line.startswith("|")]


def _offer_name(offer: ShopOffer, lang: str) -> str:
    if offer.kind is RewardKind.ITEM:
        item = next(i for i in RUN.items if i.key == offer.item_key)
        return item.names[lang]
    return i18n.t(f"consumable.name.{offer.kind.value}", lang.lower())


@LANGS
def test_every_shop_offer_is_a_row_with_its_price(lang: str) -> None:
    rows = _rows(lang)
    missing = [
        offer.key
        for offer in RUN.shop
        if not any(_offer_name(offer, lang) in row and f"| {offer.price} 🪙" in row for row in rows)
    ]
    assert missing == []


@LANGS
def test_every_pass_tier_shows_its_xp(lang: str) -> None:
    rows = _rows(lang)
    missing = [
        number
        for number, tier in enumerate(RUN.tiers, start=1)
        if not any(row.startswith(f"| {number} | {tier.xp} |") for row in rows)
    ]
    assert missing == []


@LANGS
def test_every_objective_shows_its_name_and_target(lang: str) -> None:
    rows = _rows(lang)
    missing = [
        objective.key
        for objective in RUN.objectives
        if not any(
            objective.name(lang) in row and str(objective.goal.target) in row for row in rows
        )
    ]
    assert missing == []


# Each number in the sentence that states it, so an unrelated "100" or "7" can't pass.
CANDLE_GOAL = {"EN": "snuffs {target} candles", "RU": "гасит {target} свечей"}
BONE_SET = {"EN": "win {bones} haunted rounds", "RU": "выиграйте {bones} раундов"}


@LANGS
def test_the_page_states_the_haunt_chance(lang: str) -> None:
    assert RUN.haunt is not None
    assert f"**{RUN.haunt.chance_pct}%**" in _page(lang)


@LANGS
def test_the_page_states_the_candle_goal(lang: str) -> None:
    assert RUN.group_goal is not None
    assert CANDLE_GOAL[lang].format(target=RUN.group_goal.target) in _page(lang)


@LANGS
def test_the_bone_set_row_states_its_size(lang: str) -> None:
    assert RUN.bone_set is not None
    item = next(i for i in RUN.items if i.key == RUN.bone_set.item_key)
    phrase = BONE_SET[lang].format(bones=RUN.bone_set.bones)
    assert any(item.names[lang] in row and phrase in row for row in _rows(lang))
