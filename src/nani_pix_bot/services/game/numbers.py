"""Whether a game's numbers matter (issues #344/#345): the setup preview's
"numbers count" switch and the question asked before the preview for a
number-heavy title. Matching itself reads `Game.numbers_matter` — see
state.py's is_correct_guess and MECHANICS.md's "Starting a game"."""

from nani_pix_bot.models.game import Game
from nani_pix_bot.services import matching
from nani_pix_bot.services.game.state import TitleField


def _title_variants(game: Game) -> list[str | None]:
    """The title fields only: a synonym like "MP100" is a nickname, not
    a reason to ask about the title's numbers."""
    return [getattr(game, f"title_{field.value}") for field in TitleField]


def titles_have_numbers(game: Game) -> bool:
    """Whether any of the game's titles has a digit — the preview offers
    the "numbers count" switch only then."""
    return any(title and any(char.isdigit() for char in title) for title in _title_variants(game))


def should_ask_if_numbers_matter(game: Game) -> bool:
    """Whether setup should stop to ask the creator if the numbers in a
    number-heavy title matter: not yet answered, and some title
    qualifies — see matching.should_ask_if_numbers_matter."""
    return game.numbers_matter is None and matching.should_ask_if_numbers_matter(
        _title_variants(game)
    )
