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

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.mal_link import MalCredentials
from nani_pix_bot.models.reveal_video import RevealVideo
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services.game.state import TitleVariants
from nani_pix_bot.services.reveal import celebration as reveal_celebration
from nani_pix_bot.services.reveal import effects as reveal_effects
from nani_pix_bot.services.reveal import encode as reveal_encode
from nani_pix_bot.services.reveal import pipeline as reveal_pipeline
from nani_pix_bot.services.reveal import store as reveal_store
from nani_pix_bot.services.reveal.effects import iris as reveal_iris
from nani_pix_bot.services.reveal.effects import ripple as reveal_ripple
from nani_pix_bot.services.search.mal_user import MalAnimeListEntry
from nani_pix_bot.services.version import _TelegramHTMLRenderer

if TYPE_CHECKING:
    from loguru import Record

_kept = (
    # --- False positives ---
    # Read by name: prioritized_title_field() does getattr(variants, field.value).
    TitleVariants.english,
    TitleVariants.romaji,
    TitleVariants.native,
    TitleVariants.russian,
    # mistune calls these renderer hooks by name while rendering.
    _TelegramHTMLRenderer.heading,
    _TelegramHTMLRenderer.paragraph,
    _TelegramHTMLRenderer.list_item,
    _TelegramHTMLRenderer.thematic_break,
    _TelegramHTMLRenderer.image,
    _TelegramHTMLRenderer.linebreak,
    _TelegramHTMLRenderer.block_html,
    _TelegramHTMLRenderer.block_error,
    # --- Kept on purpose (issue #239) ---
    # Written for the record, read only by people querying the database.
    Game.ended_at,
    MalCredentials.linked_at,
    TurnState.turn_opened_at,
    # Parsed from MAL's list response; kept for showing cover art later.
    MalAnimeListEntry.image_url,
    # TEMPORARY — animated reveal (#295) building blocks not yet called;
    # removed by the wiring tasks (#308/#309/#310)
    RevealEffect.IRIS,
    RevealEffect.TILE_FLIP,
    RevealEffect.RIPPLE,
    RevealEffect.GLITCH,
    RevealEffect.SHATTER,
    RevealVideo.ready_at,
    reveal_store.pick_effect,
    reveal_store.pick_image,
    reveal_store.reserve,
    reveal_store.mark_ready,
    reveal_encode.encode_part1,
    reveal_encode.to_ts,
    reveal_encode.ending_and_join,
    reveal_effects.EFFECTS,
    reveal_iris.render_part1,
    reveal_ripple.render_part1,
    reveal_celebration.badge_height,
    reveal_celebration.make_badge,
    reveal_celebration.badge_frames,
    reveal_celebration.confetti_clip,
    reveal_pipeline.pregenerate,
    reveal_pipeline.finish,
    reveal_pipeline.warm_up,
)

if TYPE_CHECKING:
    # False positive: loguru's Record exists only in its type stubs, so
    # log_context.py and logging_config.py import it under TYPE_CHECKING
    # and use it only in quoted annotations, which vulture doesn't read.
    _record: Record
