"""Yōkai Night (#473): the Halloween season run — horror, supernatural,
mythology, vampire, action, adventure and fantasy anime under a moonlit wash,
with haunted rounds, the Night Parade on 31 October, the 100 Candles goal and
its Gashadokuro boss, hexes, omikuji and the bone set. Frozen once shipped;
re-locked once while live, when #489 widened the gate."""

from types import MappingProxyType

from nani_pix_bot.models.enums import (
    ConsumableKind,
    CosmeticSlot,
    EventType,
    HexOutcome,
    Rarity,
)
from nani_pix_bot.seasons.definition import (
    CoinTable,
    Gate,
    GateTag,
    Goal,
    GoalKind,
    Item,
    Objective,
    Reward,
    RewardKind,
    RunAchievement,
    SeasonRun,
    ShopOffer,
    Tier,
    XpTable,
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
    Palette,
)

RUN_ID = "yokai_1"


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


_FLAIR, _REACTION = CosmeticSlot.NAME_FLAIR, CosmeticSlot.GUESS_REACTION
_WIN, _TRANSITION = CosmeticSlot.WIN_EFFECT, CosmeticSlot.HOST_TRANSITION
_FRAME, _BANNER, _TITLE = CosmeticSlot.HOST_FRAME, CosmeticSlot.PROFILE_BANNER, CosmeticSlot.TITLE
_COSTUME = CosmeticSlot.MASCOT_COSTUME

ITEMS = (
    Item("name_flair.jack_o_lantern", _FLAIR, "🎃", _names("Jack-o'-Lantern", "Тыквенный фонарь")),
    Item("name_flair.ghost", _FLAIR, "👻", _names("Ghost", "Призрак")),
    Item("name_flair.bat", _FLAIR, "🦇", _names("Bat", "Летучая мышь")),
    Item("name_flair.fox", _FLAIR, "🦊", _names("Fox", "Лиса")),
    Item("name_flair.candle", _FLAIR, "🕯️", _names("Candle", "Свеча")),
    Item("name_flair.chochin", _FLAIR, "🏮", _names("Chōchin", "Тётин")),
    Item("name_flair.bone", _FLAIR, "🦴", _names("Bone", "Косточка")),
    Item("name_flair.mini_skeleton", _FLAIR, "☠️", _names("Mini Skeleton", "Мини-скелет")),
    Item("guess_reaction.boo", _REACTION, "👻", _names("Boo", "Бу!")),
    Item("guess_reaction.pumpkin", _REACTION, "🎃", _names("Pumpkin", "Тыква")),
    Item("guess_reaction.imp", _REACTION, "😈", _names("Imp", "Бесёнок")),
    Item("host_frame.ofuda_strip", _FRAME, "ofuda_strip", _names("Ofuda Strip", "Полоски офуда")),
    Item("host_frame.cobweb", _FRAME, "cobweb", _names("Cobwebs", "Паутина")),
    Item(
        "host_frame.lantern_string",
        _FRAME,
        "lantern_string",
        _names("Lantern String", "Гирлянда фонарей"),
    ),
    Item("host_frame.bone_border", _FRAME, "bone_border", _names("Bone Border", "Костяная рамка")),
    Item(
        "host_transition.candle_snuff",
        _TRANSITION,
        "CANDLE_SNUFF",
        _names("Candle Snuff", "Задутая свеча"),
    ),
    Item(
        "host_transition.night_parade",
        _TRANSITION,
        "NIGHT_PARADE",
        _names("Night Parade", "Ночной парад"),
    ),
    Item(
        "host_transition.gashadokuro_rise",
        _TRANSITION,
        "GASHADOKURO_RISE",
        _names("Gashadokuro Rise", "Восстание Гасядокуро"),
    ),
    Item("win_effect.bat_burst", _WIN, "bat_burst", _names("Bat Burst", "Стая летучих мышей")),
    Item("win_effect.hitodama", _WIN, "hitodama", _names("Hitodama", "Хитодама")),
    Item("win_effect.bone_rattle", _WIN, "bone_rattle", _names("Bone Rattle", "Пляска скелета")),
    Item(
        "profile_banner.night_parade_scroll",
        _BANNER,
        "night_parade_scroll.jpg",
        _names("Night Parade Scroll", "Свиток ночного парада"),
    ),
    Item(
        "profile_banner.lantern_shrine",
        _BANNER,
        "lantern_shrine.jpg",
        _names("Lantern Shrine", "Храм фонарей"),
    ),
    Item(
        "profile_banner.gashadokuro_town",
        _BANNER,
        "gashadokuro_town.jpg",
        _names("Gashadokuro Town", "Город Гасядокуро"),
    ),
    Item("title.onmyoji", _TITLE, "", _names("Onmyōji", "Оммёдзи")),
    Item("title.yokai_hunter", _TITLE, "", _names("Yōkai Hunter", "Охотник на ёкаев")),
    Item("title.kitsune", _TITLE, "", _names("Kitsune", "Кицунэ")),
    Item(
        "title.gashadokuro_slayer",
        _TITLE,
        "",
        _names("Gashadokuro Slayer", "Победитель Гасядокуро"),
    ),
    Item(
        "mascot_costume.pastel_witch",
        _COSTUME,
        "pastel_witch",
        _names("Pastel Witch", "Пастельная ведьма"),
    ),
    Item("mascot_costume.bakeneko", _COSTUME, "bakeneko", _names("Bakeneko", "Бакэнэко")),
    Item("mascot_costume.kitsune", _COSTUME, "kitsune", _names("Kitsune", "Кицунэ")),
    Item("mascot_costume.skeleton", _COSTUME, "skeleton", _names("Skeleton", "Скелет")),
    Item(
        "mascot_costume.chochin_obake",
        _COSTUME,
        "chochin",
        _names("Chōchin-obake", "Тётин-обакэ"),
    ),
)

TIERS = (
    Tier(20, Reward.item("name_flair.jack_o_lantern"), Reward.item("mascot_costume.pastel_witch")),
    Tier(50, Reward.coins(100), Reward.item("name_flair.fox")),
    Tier(90, Reward.clue_ticket(), Reward.item("guess_reaction.imp")),
    Tier(140, Reward.item("guess_reaction.boo"), Reward.item("mascot_costume.bakeneko")),
    Tier(190, Reward.item("name_flair.bat"), Reward.item("title.kitsune")),
    Tier(240, Reward.item("win_effect.hitodama"), Reward.item("host_frame.lantern_string")),
    Tier(300, Reward.omamori(), Reward.item("mascot_costume.kitsune")),
    Tier(
        360,
        Reward.item("profile_banner.night_parade_scroll"),
        Reward.item("host_transition.night_parade"),
    ),
    Tier(420, Reward.coins(150), Reward.currency(100)),
    Tier(490, Reward.item("host_frame.cobweb"), Reward.stamp_plus()),
    Tier(570, Reward.item("title.onmyoji"), Reward.item("profile_banner.lantern_shrine")),
    Tier(650, Reward.stamp(), Reward.item("mascot_costume.skeleton")),
)

SHOP = (
    ShopOffer.item("name_flair.ghost", 25),
    ShopOffer.item("name_flair.candle", 25),
    ShopOffer.item("name_flair.chochin", 25),
    ShopOffer.item("name_flair.bone", 25),
    ShopOffer.item("guess_reaction.pumpkin", 35),
    ShopOffer.item("host_frame.ofuda_strip", 45),
    ShopOffer.item("host_frame.bone_border", 55),
    ShopOffer.item("host_transition.candle_snuff", 75),
    ShopOffer.item("host_transition.gashadokuro_rise", 95),
    ShopOffer.item("win_effect.bat_burst", 45),
    ShopOffer.item("win_effect.bone_rattle", 55),
    ShopOffer.item("profile_banner.gashadokuro_town", 55),
    ShopOffer.item("title.yokai_hunter", 45),
    ShopOffer.item("mascot_costume.chochin_obake", 85),
    ShopOffer.clue_ticket(30, 2),
    ShopOffer.consumable(RewardKind.OFUDA, 20, 8),
)

OBJECTIVES = (
    Objective(
        "kimodameshi",
        _names("Kimodameshi", "Кимодамэси"),
        Goal(GoalKind.WINS, 1, max_stage=2),
        xp=60,
        coins=70,
    ),
    Objective(
        "lantern_keeper",
        _names("Lantern Keeper", "Хранитель фонарей"),
        Goal(GoalKind.HOSTED, 2),
        xp=40,
        coins=60,
    ),
    Objective(
        "spirit_chaser",
        _names("Spirit Chaser", "Охотник за духами"),
        Goal(GoalKind.WINS, 3),
        xp=80,
        coins=100,
    ),
    Objective(
        "night_walker",
        _names("Night Walker", "Ночной странник"),
        Goal(GoalKind.DAYS_PLAYED, 5),
        xp=50,
        coins=70,
    ),
    Objective(
        "hundred_tales",
        _names("Hundred Tales", "Сто страшных историй"),
        Goal(GoalKind.GUESSED_IN, 8),
        xp=40,
        coins=60,
    ),
    Objective(
        "bone_collector",
        _names("Bone Collector", "Собиратель костей"),
        Goal(GoalKind.HAUNTED_WINS, 2),
        xp=60,
        coins=60,
    ),
    Objective(
        "exorcist",
        _names("Exorcist", "Экзорцист"),
        Goal(GoalKind.HEXES_BLOCKED, 1),
        xp=30,
        coins=40,
    ),
)

_MYTHOLOGY = GateTag("theme", "Mythology", 6, shikimori_id=6)
_SUPERNATURAL = GateTag("genre", "Supernatural", 37, shikimori_id=37)

GROUP_GOAL = GroupGoal(
    target=100,
    per_game=1,
    per_haunted=2,
    per_event_day=3,
    milestone_every=25,
    boss_xp_multiplier=2,
    boss_coin_multiplier=3,
    contributor_coins=80,
    contributor_item_key="title.gashadokuro_slayer",
)

_ENDS = (EventType.GAME_WON, EventType.GAME_UNSOLVED)
_B, _S, _G, _P = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD, Rarity.PLATINUM
_LADDER = (_B, _S, _G)
_PASS_AND_SHOP_IDS = frozenset(
    f"{RUN_ID}/{key}"
    for key in (
        *(
            reward.item_key
            for tier in TIERS
            for reward in (tier.free, tier.premium)
            if reward is not None and reward.kind is RewardKind.ITEM
        ),
        *(offer.item_key for offer in SHOP if offer.kind is RewardKind.ITEM),
    )
)


def _wins(history) -> list[dict]:
    """The data of each game you won; a re-finished game counts once."""
    return list({e.game_id: e.data for e in history.mine(EventType.GAME_WON)}.values())


def _blocks(history) -> int:
    return len(history.mine(EventType.HEX_BLOCKED))


def _hexes_landed(history) -> int:
    """Hexes cast on others that their Omamori didn't block (no omikuji self-prank)."""
    casts = history.mine(EventType.HEX_CAST)
    return sum(
        1
        for e in casts
        if e.data.get("outcome") != HexOutcome.BLOCKED.value and not e.data.get("self_prank")
    )


def _haunted_wins(history) -> int:
    return sum(1 for data in _wins(history) if data.get("haunted"))


def _ofuda_saves(history) -> int:
    return len(history.mine(EventType.OFUDA_USED))


def _draws(history, kind: OmikujiKind | None = None) -> int:
    drawn = history.mine(EventType.OMIKUJI_DRAWN)
    return sum(1 for e in drawn if kind is None or e.data.get("outcome") == kind.value)


def _candle_weight(data: dict) -> int:
    """The candles a finished game lit (services/seasons/group_goal.py's rule)."""
    if data.get("boss_game"):
        return 0
    if data.get("event_day"):
        return GROUP_GOAL.per_event_day
    if data.get("haunted"):
        return GROUP_GOAL.per_haunted
    return GROUP_GOAL.per_game


def _guessed_or_won_ends(history) -> list:
    """The end events of every game you guessed in or won (a /setwinner or
    vote winner may not have guessed)."""
    guessed = {e.game_id for e in history.mine(EventType.GUESS)}
    ends = [e for e in history.group(*_ENDS) if e.game_id in guessed]
    return ends + history.mine(EventType.GAME_WON)


def _my_ended_games(history) -> dict[int | None, dict]:
    """Each finished game you guessed in, won or hosted (not HARD MODE's
    bot host) — the 100 Candles contributors' rule — by game id."""
    ends = _guessed_or_won_ends(history)
    ends += [e for e in history.about_me(*_ENDS) if not e.data.get("hard_mode")]
    return {e.game_id: e.data for e in ends}


def _candles(history) -> int:
    return sum(_candle_weight(data) for data in _my_ended_games(history).values())


def _event_day_wins(history) -> int:
    return sum(1 for data in _wins(history) if data.get("event_day"))


def _boss_game(history) -> int:
    """A Gashadokuro boss game you guessed in or won has ended."""
    return 1 if any(e.data.get("boss_game") for e in _guessed_or_won_ends(history)) else 0


def _boss_won(history) -> int:
    return 1 if any(data.get("boss_game") for data in _wins(history)) else 0


def _daikichi(history) -> int:
    return min(1, _draws(history, OmikujiKind.DAIKICHI))


def _kyo_draws(history) -> int:
    return _draws(history, OmikujiKind.KYO)


def _hexed_win(history) -> int:
    return 1 if any(data.get("hexed") for data in _wins(history)) else 0


def _every_cosmetic(history) -> int:
    """Every pass and shop cosmetic: the goal title and the bone-set ☠️ are left out."""
    owned = {e.data["item_id"] for e in history.mine(EventType.ITEM_GAINED)}
    return 1 if owned >= _PASS_AND_SHOP_IDS else 0


ACHIEVEMENTS = (
    RunAchievement(
        "curse_breaker",
        _names("Curse Breaker", "Разрушитель проклятий"),
        _names("Block hexes with an Omamori", "Отражайте проклятия омамори"),
        _B,
        frozenset({EventType.HEX_BLOCKED}),
        _blocks,
        tiers=(1, 2, 4),
        rarities=_LADDER,
    ),
    RunAchievement(
        "hex_master",
        _names("Hex Master", "Мастер проклятий"),
        _names("Land hexes on other players", "Накладывайте проклятия на других игроков"),
        _B,
        frozenset({EventType.HEX_CAST}),
        _hexes_landed,
        tiers=(1, 5, 15),
        rarities=_LADDER,
    ),
    RunAchievement(
        "ghost_hunter",
        _names("Ghost Hunter", "Охотник на призраков"),
        _names("Win haunted games", "Побеждайте в раундах, где водятся призраки"),
        _B,
        frozenset({EventType.GAME_WON}),
        _haunted_wins,
        tiers=(1, 5, 10),
        rarities=_LADDER,
    ),
    RunAchievement(
        "paper_shield",
        _names("Paper Shield", "Бумажный щит"),
        _names("Have an Ofuda save your miss", "Пусть офуда спасёт вашу ошибку"),
        _B,
        frozenset({EventType.OFUDA_USED}),
        _ofuda_saves,
        tiers=(1, 3, 8),
        rarities=_LADDER,
    ),
    RunAchievement(
        "fortune_seeker",
        _names("Fortune Seeker", "Искатель удачи"),
        _names("Draw omikuji slips", "Тяните омикудзи"),
        _B,
        frozenset({EventType.OMIKUJI_DRAWN}),
        _draws,
        tiers=(3, 7, 12),
        rarities=_LADDER,
    ),
    RunAchievement(
        "candle_snuffer",
        _names("Candle Snuffer", "Гаситель свечей"),
        _names("Put out candles with your games", "Гасите свечи своими раундами"),
        _B,
        frozenset(_ENDS),
        _candles,
        tiers=(5, 15, 30),
        rarities=_LADDER,
    ),
    RunAchievement(
        "night_parade",
        _names("Night Parade", "Ночной парад"),
        _names("Win games on the Night Parade", "Побеждайте в Ночной парад"),
        _B,
        frozenset({EventType.GAME_WON}),
        _event_day_wins,
        tiers=(1, 2, 3),
        rarities=_LADDER,
    ),
    RunAchievement(
        "gashadokuro",
        _names("Gashadokuro", "Гасядокуро"),
        _names("Play in the Gashadokuro boss game", "Сыграйте в битве против Гасядокуро"),
        _S,
        frozenset(_ENDS),
        _boss_game,
    ),
    RunAchievement(
        "gashadokuro_bane",
        _names("Bane of the Gashadokuro", "Гроза Гасядокуро"),
        _names("Win the Gashadokuro boss game", "Победите в битве против Гасядокуро"),
        _P,
        frozenset({EventType.GAME_WON}),
        _boss_won,
        hidden=True,
    ),
    RunAchievement(
        "daikichi",
        _names("Daikichi", "Дайкити"),
        _names("Draw the great blessing", "Вытяните большую удачу"),
        _B,
        frozenset({EventType.OMIKUJI_DRAWN}),
        _daikichi,
        hidden=True,
    ),
    RunAchievement(
        "cursed_luck",
        _names("Cursed Luck", "Проклятая удача"),
        _names("Draw Kyō three times", "Вытяните Кё три раза"),
        _B,
        frozenset({EventType.OMIKUJI_DRAWN}),
        _kyo_draws,
        hidden=True,
        tiers=(3,),
    ),
    RunAchievement(
        "hexed_and_happy",
        _names("Hexed and Happy", "Проклят, но счастлив"),
        _names("Win a game while carrying a hex", "Победите, будучи под проклятием"),
        _S,
        frozenset({EventType.GAME_WON}),
        _hexed_win,
        hidden=True,
    ),
    RunAchievement(
        "hyakki_yagyo",
        _names("Hyakki Yagyō", "Хякки ягё"),
        _names(
            "Own every Yōkai Night cosmetic from the pass and the shop",
            "Соберите все косметические предметы Ночи ёкаев из пропуска и магазина",
        ),
        _P,
        frozenset({EventType.ITEM_GAINED}),
        _every_cosmetic,
        hidden=True,
    ),
)

RUN = SeasonRun(
    run_id=RUN_ID,
    names=_names("Yōkai Night", "Ночь ёкаев"),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(
            GateTag("genre", "Horror", 14, shikimori_id=14),
            _SUPERNATURAL,
            _MYTHOLOGY,
            GateTag("theme", "Vampire", 32, shikimori_id=32),
            GateTag("genre", "Action", 1, shikimori_id=1),
            GateTag("genre", "Adventure", 2, shikimori_id=2),
            GateTag("genre", "Fantasy", 10, shikimori_id=10),
        ),
        description=_names(
            "horror, supernatural, mythology, vampire, action, adventure or fantasy anime",
            "ужасы, мистику, мифологию, вампиров, экшен, приключения или фэнтези",
        ),
    ),
    items=ITEMS,
    coins=CoinTable(win=40, host_solved=25, host_unsolved=15, first_guess=15),
    tiers=TIERS,
    premium_price=300,
    coins_per_currency=2,
    shop=SHOP,
    modifiers=MappingProxyType({}),
    style="moonlit",
    objectives=OBJECTIVES,
    achievements=ACHIEVEMENTS,
    palette=Palette(ribbon=(236, 112, 24), title_from=(255, 158, 54), title_to=(150, 92, 230)),
    emoji="🎃",
    mascot_costume="pumpkin",
    haunt=Haunt(chance_pct=25, twists=tuple(HauntTwist), coin_multiplier=2),
    event_day=EventDay(
        10,
        31,
        _names("Night Parade", "Ночной парад"),
        gate=Gate(
            any_of=(_MYTHOLOGY, _SUPERNATURAL),
            description=_names("mythology or supernatural anime", "мифологию или мистику"),
        ),
        coin_multiplier=2,
        all_haunted=True,
    ),
    group_goal=GROUP_GOAL,
    hexes=(
        HexRule(HexKind.NOPPERABO, price=20, cooldown_minutes=30, duration_hours=24),
        HexRule(HexKind.CHOCHIN, price=20, cooldown_minutes=30, duration_hours=24),
        HexRule(HexKind.KAPPA, price=40, cooldown_minutes=60, amount=50),
        HexRule(HexKind.KITSUNE, price=30, cooldown_minutes=60, amount=15),
    ),
    omikuji=(
        OmikujiOutcome(
            OmikujiKind.DAIKICHI, weight=15, coins=30, consumable=ConsumableKind.OMAMORI
        ),
        OmikujiOutcome(OmikujiKind.KICHI, weight=30, coins=15),
        OmikujiOutcome(OmikujiKind.SHOKICHI, weight=35, coins=5),
        OmikujiOutcome(OmikujiKind.KYO, weight=20, prank=True),
    ),
    bone_set=BoneSet(bones=7, item_key="name_flair.mini_skeleton"),
    guide_page="yokai-night",
)
