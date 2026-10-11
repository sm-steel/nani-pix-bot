from types import MappingProxyType

from nani_pix_bot.models.enums import CosmeticSlot, EventType, Rarity
from nani_pix_bot.seasons.definition import (
    CoinTable,
    Gate,
    GateTag,
    Goal,
    GoalKind,
    Item,
    Modifier,
    Objective,
    Reward,
    RunAchievement,
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

OBJECTIVES = (
    Objective(
        "hanami_host",
        _names("Hanami Host", "Хозяин ханами"),
        Goal(GoalKind.HOSTED, 2),
        xp=40,
        coins=30,
    ),
    Objective(
        "first_bloom",
        _names("First Bloom", "Первый цвет"),
        Goal(GoalKind.WINS, 1, max_stage=2),
        xp=60,
        coins=40,
    ),
    Objective(
        "study_group",
        _names("Study Group", "Учебная группа"),
        Goal(GoalKind.SHARED_CLUES, 1),
        xp=30,
        coins=20,
    ),
    Objective(
        "senpai_notice",
        _names("Senpai Notice", "Сэмпай заметил"),
        Goal(GoalKind.TIPS, 1),
        xp=20,
        coins=20,
    ),
)


def _two_wins_one_day(history) -> int:
    days = [e.occurred_at.astimezone(history.tz).date() for e in history.mine(EventType.GAME_WON)]
    return 1 if any(days.count(day) >= 2 for day in set(days)) else 0


ACHIEVEMENTS = (
    RunAchievement(
        "double_date",
        _names("Double Date", "Двойное свидание"),
        _names("Win two season games in one day", "Выиграйте две игры сезона за один день"),
        Rarity.SILVER,
        frozenset({EventType.GAME_WON}),
        _two_wins_one_day,
    ),
)

RUN = SeasonRun(
    run_id="demo_1",
    names=MappingProxyType({"EN": "Demo Season", "RU": "Демо-сезон"}),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(GateTag("genre", "Romance", 22, shikimori_id=22),),
        description=MappingProxyType({"EN": "romance anime", "RU": "романтику"}),
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
    style="plain",
    objectives=OBJECTIVES,
    achievements=ACHIEVEMENTS,
)
