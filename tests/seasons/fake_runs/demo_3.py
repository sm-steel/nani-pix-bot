"""A third fake run: demo_1 plus every optional mechanic switched on."""

from dataclasses import replace
from types import MappingProxyType

from nani_pix_bot.models.enums import ConsumableKind, CosmeticSlot
from nani_pix_bot.seasons.definition import (
    Gate,
    GateTag,
    Item,
    Reward,
    RewardKind,
    ShopOffer,
    Tier,
)
from nani_pix_bot.seasons.mechanics import (
    BoneSet,
    EventDay,
    GroupGoal,
    Haunt,
    HauntTwist,
    HexKind,
    HexRule,
    OmikujiKind,
    OmikujiOutcome,
)
from tests.seasons.fake_runs import demo_1


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


CANDLE = Item("name_flair.candle", CosmeticSlot.NAME_FLAIR, "🕯", _names("Candle", "Свеча"))
BONE = Item("name_flair.bone", CosmeticSlot.NAME_FLAIR, "🦴", _names("Bone", "Кость"))

RUN = replace(
    demo_1.RUN,
    run_id="demo_3",
    names=_names("Third Demo", "Третий демо-сезон"),
    items=(*demo_1.RUN.items, CANDLE, BONE),
    tiers=(*demo_1.RUN.tiers, Tier(200, Reward.omamori(), None)),
    shop=(*demo_1.RUN.shop, ShopOffer.consumable(RewardKind.OFUDA, 40, 2)),
    haunt=Haunt(25, tuple(HauntTwist)),
    event_day=EventDay(
        10,
        31,
        _names("Halloween", "Хэллоуин"),
        gate=Gate(
            any_of=(GateTag("genre", "Horror", 14, shikimori_id=14),),
            description=_names("horror anime", "ужасы"),
        ),
    ),
    group_goal=GroupGoal(target=10, contributor_coins=20, contributor_item_key=CANDLE.key),
    hexes=(
        HexRule(HexKind.NOPPERABO, price=20, cooldown_minutes=30, duration_hours=2),
        HexRule(HexKind.CHOCHIN, price=20, cooldown_minutes=30, duration_hours=2),
        HexRule(HexKind.KAPPA, price=30, cooldown_minutes=60, amount=30),
        HexRule(HexKind.KITSUNE, price=30, cooldown_minutes=60, amount=10),
    ),
    omikuji=(
        OmikujiOutcome(OmikujiKind.DAIKICHI, 1, coins=50),
        OmikujiOutcome(OmikujiKind.KICHI, 3, coins=20, consumable=ConsumableKind.OFUDA),
        OmikujiOutcome(OmikujiKind.SHOKICHI, 4, coins=5),
        OmikujiOutcome(OmikujiKind.KYO, 2, prank=True),
    ),
    bone_set=BoneSet(3, BONE.key),
)
