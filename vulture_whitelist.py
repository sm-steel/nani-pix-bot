"""Names vulture reports as unused that are kept on purpose (issue #240).

vulture checks this file together with src/ (see [tool.vulture] in
pyproject.toml): a name used here counts as used. It is ordinary Python,
so ruff and ty check it like any other file. It is never imported.

Only two kinds of entry belong here, each agreed for that one name:
- a false positive, where the code really is used but vulture can't see it;
- something deliberately kept even though no code reads it.
Anything else vulture reports is dead code: remove it or wire it up.
"""

from typing import TYPE_CHECKING

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.mal_link import MalCredentials
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.seasons.definition import GoalKind, Reward
from nani_pix_bot.seasons.mechanics import HauntTwist, OmikujiKind, Palette
from nani_pix_bot.services.game.state import TitleVariants
from nani_pix_bot.services.search.mal_user import MalAnimeListEntry

if TYPE_CHECKING:
    from loguru import Record

_kept = (
    # --- False positives ---
    # Read by name: prioritized_title_field() does getattr(variants, field.value).
    TitleVariants.english,
    TitleVariants.romaji,
    TitleVariants.native,
    TitleVariants.russian,
    # --- Kept on purpose (issue #239) ---
    # Written for the record, read only by people querying the database.
    Game.ended_at,
    MalCredentials.linked_at,
    TurnState.turn_opened_at,
    # Parsed from MAL's list response; kept for showing cover art later.
    MalAnimeListEntry.image_url,
    # Halloween: wired by P1 Task 2
    Palette.title_from,
    Palette.title_to,
    # Halloween: wired by P3 (#468)
    HauntTwist.KITSUNE_MIRROR,
    HauntTwist.TANUKI_LEAF,
    HauntTwist.YUREI_DOUBLE,
    HauntTwist.KYOKOTSU_XRAY,
    GoalKind.HAUNTED_WINS,
    # Halloween: wired by P4 (#469)
    OmikujiKind.DAIKICHI,
    OmikujiKind.KICHI,
    OmikujiKind.SHOKICHI,
    Reward.ofuda,
    Reward.omamori,
    # Halloween: wired by P5 (#470)
    GoalKind.HEXES_BLOCKED,
)

if TYPE_CHECKING:
    # False positive: loguru's Record exists only in its type stubs, so
    # log_context.py and logging_config.py import it under TYPE_CHECKING
    # and use it only in quoted annotations, which vulture doesn't read.
    _record: Record
