"""Sakura Season (#405, #407): the first real season run — slice-of-life,
romance and school anime under a watercolor wash. Frozen once shipped."""

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

RUN_ID = "sakura_1"


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


_FLAIR, _REACTION = CosmeticSlot.NAME_FLAIR, CosmeticSlot.GUESS_REACTION
_WIN, _TRANSITION = CosmeticSlot.WIN_EFFECT, CosmeticSlot.HOST_TRANSITION
_FRAME, _BANNER, _TITLE = CosmeticSlot.HOST_FRAME, CosmeticSlot.PROFILE_BANNER, CosmeticSlot.TITLE

ITEMS = (
    Item("name_flair.blossom", _FLAIR, "🌸", _names("Blossom", "Цветок сакуры")),
    Item("name_flair.wind_chime", _FLAIR, "🎐", _names("Wind Chime", "Фурин")),
    Item("name_flair.matcha", _FLAIR, "🍵", _names("Matcha", "Матча")),
    Item("name_flair.dango", _FLAIR, "🍡", _names("Dango", "Данго")),
    Item("guess_reaction.smitten", _REACTION, "🥰", _names("Smitten", "Влюблённость")),
    Item("guess_reaction.strawberry", _REACTION, "🍓", _names("Strawberry", "Клубника")),
    Item("guess_reaction.heart_arrow", _REACTION, "💘", _names("Cupid", "Купидон")),
    Item("win_effect.petal_drift", _WIN, "petal_drift", _names("Petal Drift", "Лепестки на ветру")),
    Item("win_effect.sakura_storm", _WIN, "sakura_storm", _names("Sakura Storm", "Буря сакуры")),
    Item(
        "host_transition.spring_breeze",
        _TRANSITION,
        "SPRING_BREEZE",
        _names("Spring Breeze", "Весенний ветер"),
    ),
    Item(
        "host_transition.blossom_branch",
        _TRANSITION,
        "BLOSSOM_BRANCH",
        _names("Blossom Branch", "Ветка сакуры"),
    ),
    Item("host_frame.paper", _FRAME, "paper", _names("Paper", "Бумага")),
    Item("host_frame.gold_washi", _FRAME, "gold_washi", _names("Gold Washi", "Золотая васи")),
    Item("host_frame.washi", _FRAME, "washi", _names("Washi", "Васи")),
    Item(
        "profile_banner.night_hanami",
        _BANNER,
        "night_hanami.jpg",
        _names("Night Hanami", "Ночное ханами"),
    ),
    Item(
        "profile_banner.blossom_avenue",
        _BANNER,
        "blossom_avenue.jpg",
        _names("Blossom Avenue", "Аллея сакуры"),
    ),
    Item(
        "profile_banner.school_rooftop",
        _BANNER,
        "school_rooftop.jpg",
        _names("School Rooftop", "Школьная крыша"),
    ),
    Item("title.hanami_host", _TITLE, "", _names("Hanami Host", "Хозяин ханами")),
    Item("title.sakura", _TITLE, "", _names("Sakura", "Сакура")),
    Item("title.senpai", _TITLE, "", _names("Senpai", "Сэмпай")),
)

TIERS = (
    Tier(20, Reward.item("name_flair.blossom"), Reward.item("profile_banner.night_hanami")),
    Tier(50, Reward.coins(60), Reward.coins(40)),
    Tier(90, Reward.clue_ticket(), Reward.item("guess_reaction.strawberry")),
    Tier(130, Reward.item("guess_reaction.smitten"), Reward.item("name_flair.wind_chime")),
    Tier(170, Reward.coins(70), Reward.item("title.hanami_host")),
    Tier(210, Reward.item("win_effect.petal_drift"), Reward.item("host_frame.gold_washi")),
    Tier(260, Reward.item("profile_banner.blossom_avenue"), Reward.coins(50)),
    Tier(310, Reward.item("name_flair.matcha"), Reward.item("host_transition.spring_breeze")),
    Tier(360, Reward.coins(80), Reward.currency(100)),
    Tier(420, Reward.item("host_frame.paper"), Reward.stamp_plus()),
    Tier(490, Reward.item("title.sakura"), Reward.coins(60)),
    Tier(560, Reward.stamp(), Reward.item("win_effect.sakura_storm")),
)

SHOP = (
    ShopOffer.item("host_transition.blossom_branch", 120),
    ShopOffer.item("host_frame.washi", 80),
    ShopOffer.item("name_flair.dango", 40),
    ShopOffer.item("guess_reaction.heart_arrow", 50),
    ShopOffer.item("profile_banner.school_rooftop", 90),
    ShopOffer.item("title.senpai", 100),
    ShopOffer.clue_ticket(30, 2),
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
        "club_activity",
        _names("Club Activity", "Клубная деятельность"),
        Goal(GoalKind.GUESSED_IN, 5),
        xp=40,
        coins=30,
    ),
    Objective(
        "confession",
        _names("Confession", "Признание"),
        Goal(GoalKind.WINS, 3),
        xp=80,
        coins=50,
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
    Objective(
        "petal_days",
        _names("Petal Days", "Дни лепестков"),
        Goal(GoalKind.DAYS_PLAYED, 4),
        xp=50,
        coins=40,
    ),
)

_ENDS = (EventType.GAME_WON, EventType.GAME_UNSOLVED)
_PARTY_GUESSERS = 4
_ITEM_IDS = frozenset(f"{RUN_ID}/{item.key}" for item in ITEMS)


def _two_wins_one_day(history) -> int:
    days = [e.occurred_at.astimezone(history.tz).date() for e in history.mine(EventType.GAME_WON)]
    return 1 if any(days.count(day) >= 2 for day in set(days)) else 0


def _party_game(history) -> int:
    guessed = {e.game_id for e in history.mine(EventType.GUESS)}
    ends = [e for e in history.group(*_ENDS) if e.game_id in guessed] + history.about_me(*_ENDS)
    return 1 if any(e.data.get("distinct_guessers", 0) >= _PARTY_GUESSERS for e in ends) else 0


def _every_cosmetic(history) -> int:
    owned = {e.data["item_id"] for e in history.mine(EventType.ITEM_GAINED)}
    return 1 if owned >= _ITEM_IDS else 0


ACHIEVEMENTS = (
    RunAchievement(
        "kokuhaku",
        _names("Kokuhaku", "Кокухаку"),
        _names("Win two season games on the same day", "Выиграйте две игры сезона в один день"),
        Rarity.SILVER,
        frozenset({EventType.GAME_WON}),
        _two_wins_one_day,
    ),
    RunAchievement(
        "hanami_party",
        _names("Hanami Party", "Ханами-вечеринка"),
        _names(
            "Play a season game with 4 or more guessers",
            "Сыграйте в игру сезона, где угадывали 4 и больше игроков",
        ),
        Rarity.BRONZE,
        frozenset(_ENDS),
        _party_game,
    ),
    RunAchievement(
        "full_bloom",
        _names("Full Bloom", "Полный расцвет"),
        _names(
            "Own every Sakura Season cosmetic",
            "Соберите все косметические предметы Сезона сакуры",
        ),
        Rarity.PLATINUM,
        frozenset({EventType.ITEM_GAINED}),
        _every_cosmetic,
        hidden=True,
    ),
)

RUN = SeasonRun(
    run_id=RUN_ID,
    names=_names("Sakura Season", "Сезон сакуры"),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(
            GateTag("genre", "Slice of Life", 36, shikimori_id=36),
            GateTag("genre", "Romance", 22, shikimori_id=22),
            GateTag("theme", "School", 23, shikimori_id=23),
        ),
        description=_names(
            "slice-of-life, romance or school anime", "повседневность, романтику или школу"
        ),
    ),
    items=ITEMS,
    coins=CoinTable(win=25, host_solved=15, host_unsolved=5, first_guess=5),
    tiers=TIERS,
    premium_price=300,
    coins_per_currency=2,
    shop=SHOP,
    modifiers=MappingProxyType({Modifier.CLUE_DISCOUNT_PCT: 25}),
    style="watercolor",
    objectives=OBJECTIVES,
    achievements=ACHIEVEMENTS,
)
