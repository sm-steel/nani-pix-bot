from types import MappingProxyType

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons.definition import (
    CoinTable,
    Gate,
    GateTag,
    Item,
    Modifier,
    Reward,
    SeasonRun,
    ShopOffer,
    Tier,
    XpTable,
)


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


ITEMS = (
    Item("name_flair.blossom", CosmeticSlot.NAME_FLAIR, "🌸", _names("Blossom", "Цветок")),
    Item("guess_reaction.love", CosmeticSlot.GUESS_REACTION, "🥰", _names("Love", "Любовь")),
    Item("host_frame.border", CosmeticSlot.HOST_FRAME, "simple_border", _names("Border", "Рамка")),
    Item("host_transition.iris", CosmeticSlot.HOST_TRANSITION, "IRIS", _names("Iris", "Ирис")),
    Item(
        "win_effect.confetti", CosmeticSlot.WIN_EFFECT, "confetti", _names("Confetti", "Конфетти")
    ),
    Item("name_flair.dango", CosmeticSlot.NAME_FLAIR, "🍡", _names("Dango", "Данго")),
    Item("title.demo", CosmeticSlot.TITLE, "", _names("Demo Star", "Демо-звезда")),
)

RUN = SeasonRun(
    run_id="demo_1",
    names=MappingProxyType({"EN": "Demo Season", "RU": "Демо-сезон"}),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(GateTag("genre", "Romance", 22, shikimori_id=22),),
        description=MappingProxyType({"EN": "Romance", "RU": "Романтика"}),
    ),
    items=ITEMS,
    coins=CoinTable(win=25, host_solved=15, host_unsolved=5, first_guess=5),
    tiers=(
        Tier(20, Reward.item("name_flair.blossom"), Reward.coins(40)),
        Tier(60, Reward.coins(60), Reward.item("guess_reaction.love")),
        Tier(120, Reward.clue_ticket(), Reward.currency(50)),
    ),
    premium_price=150,
    shop=(
        ShopOffer.item("name_flair.dango", 40),
        ShopOffer.item("host_frame.border", 80),
        ShopOffer.clue_ticket(30, 2),
    ),
    modifiers=MappingProxyType({Modifier.CLUE_DISCOUNT_PCT: 25}),
)
