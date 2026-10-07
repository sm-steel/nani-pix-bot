"""Every achievement, in display order (spec §4)."""

from nani_pix_bot.models.enums import EventType, Rarity
from nani_pix_bot.services.achievements import conditions as c
from nani_pix_bot.services.achievements import ladders as ld
from nani_pix_bot.services.achievements.definitions import Definition, Kind, ProgressFn, climb

E = EventType
B, S, G, P = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD, Rarity.PLATINUM
_ACTIVE = frozenset({E.GUESS, E.CLUE_PURCHASED, E.VOTE_COUNTED, E.GAME_ACTIVATED})
_WON = frozenset({E.GAME_WON})
_ENDED = frozenset({E.GAME_WON, E.GAME_UNSOLVED})
_MONEY = frozenset({E.CURRENCY_MOVED})


def _ladder(
    key: str, tiers: tuple[int, ...], triggers: frozenset[EventType], progress: ProgressFn
) -> Definition:
    return Definition(key, Kind.LADDER, tiers, climb(len(tiers)), triggers, progress)


def _once(key: str, rarity: Rarity, trigger: EventType, progress: ProgressFn) -> Definition:
    return Definition(key, Kind.ONE_SHOT, (1,), (rarity,), frozenset({trigger}), progress)


def _hidden(key: str, rarity: Rarity, trigger: EventType, progress: ProgressFn) -> Definition:
    triggers = frozenset({trigger})
    return Definition(key, Kind.ONE_SHOT, (1,), (rarity,), triggers, progress, hidden=True)


def _no_progress(_history: object) -> int:
    return 0


def _champion(key: str, rarity: Rarity) -> Definition:
    return Definition(key, Kind.PERIOD, (1,), (rarity,), frozenset(), _no_progress)


CATALOGUE: tuple[Definition, ...] = (
    Definition("sharpshooter", Kind.LADDER, (1, 5, 10, 25, 50, 100), climb(6), _WON, ld.wins, 100),
    Definition(
        "storyteller", Kind.LADDER, (1, 5, 10, 25, 50), climb(5), _WON, ld.hosted_solved, 50
    ),
    _ladder("persistent", (5, 25, 50, 100, 250), frozenset({E.GUESS}), ld.games_guessed),
    _ladder("pixel_magnate", (500, 1000, 2500, 5000, 10000), _MONEY, ld.earned),
    _ladder("big_spender", (500, 2000, 5000, 10000), _MONEY | {E.BOUNTY_SETTLED}, ld.spent),
    _ladder("clue_collector", (5, 25, 100), frozenset({E.CLUE_PURCHASED}), ld.clues_bought),
    _ladder("patron", (100, 500, 2000), _MONEY, ld.tipped),
    _ladder("regular", (7, 30, 100, 365), _ACTIVE, ld.active_days),
    _ladder("unbroken", (7, 14, 30), _ACTIVE, ld.longest_streak),
    _ladder("hard_mode_hero", (1, 5, 10, 25), _WON, ld.hard_mode_wins),
    _ladder("civic_duty", (1, 5, 20), frozenset({E.VOTE_COUNTED}), ld.ballots),
    _ladder("eagle_eye", (1, 5, 10), _WON, ld.stage_one_wins),
    _ladder("stumper", (1, 3, 10), frozenset({E.GAME_UNSOLVED}), ld.stumped),
    _ladder("hat_trick", (3, 5), _ENDED, ld.win_streak),
    Definition("bounty_hunter", Kind.LADDER, (100, 250, 500), (B, S, G), _MONEY, ld.biggest_pot),
    Definition("purist", Kind.LADDER, (1, 5), (S, G), _WON, ld.purist_wins),
    _ladder("wordsmith", (1, 10, 25), frozenset({E.GUESS}), ld.warm_guesses),
    _ladder("on_cue", (1, 10, 25), _MONEY, ld.prompt_starts),
    _once("kingmaker", B, E.VOTE_COUNTED, c.kingmaker),
    _once("clutch", G, E.GAME_WON, c.clutch),
    _once("first_try", S, E.GAME_WON, c.first_try),
    _once("speed_demon", G, E.GAME_WON, c.speed_demon),
    _once("comeback", B, E.GAME_WON, c.comeback),
    Definition("crowd_pleaser", Kind.ONE_SHOT, (1,), (S,), _ENDED, c.crowd_pleaser),
    _once("peoples_champion", S, E.GAME_WON, c.peoples_champion),
    _once("shopaholic", S, E.CLUE_PURCHASED, c.shopaholic),
    Definition("explorer", Kind.ONE_SHOT, (5,), (S,), frozenset({E.GAME_ACTIVATED}), c.explorer),
    Definition("pioneer", Kind.GROUP_UNIQUE, (1,), (P,), _WON, c.group_wins),
    Definition(
        "milestone_keeper",
        Kind.GROUP_UNIQUE,
        (100, 250, 500, 1000),
        (G, G, P, P),
        _WON,
        c.group_wins,
        500,
    ),
    _champion("champion_week", S),
    _champion("champion_month", G),
    _champion("champion_year", P),
    _hidden("dethroned", B, E.OVERTHROWN, c.dethroned),
    _hidden("so_close", S, E.GUESS, c.so_close),
    _hidden("penny_pincher", B, E.CURRENCY_MOVED, c.penny_pincher),
)

_BY_KEY = {d.key: d for d in CATALOGUE}


def get(key: str) -> Definition:
    return _BY_KEY[key]


def triggered_by(event_type: EventType) -> tuple[Definition, ...]:
    return tuple(d for d in CATALOGUE if event_type in d.triggers)
