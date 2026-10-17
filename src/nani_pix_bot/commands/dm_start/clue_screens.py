"""The clue screenshot panel's screen (issue #452), shared by the panel
itself (clue_panel.py) and by the gallery that adds to the author's list
from a source (clue_gallery.py), which re-shows it after a pick."""

from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup

from nani_pix_bot.commands.dm_start.clue_keyboards import clue_panel_keyboard
from nani_pix_bot.commands.dm_start.keyboards import clue_mode_name
from nani_pix_bot.models.enums import ClueScreenshotMode, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.clues.author_list import AddResult

Screen = tuple[str, InlineKeyboardMarkup | None]

# The steps a panel or gallery button still acts on: the preview, or the
# upload prompt the panel itself opened.
_PANEL_STEPS = frozenset({SetupStep.CONFIRMING, SetupStep.AWAITING_CLUE_PHOTO})

_ADD_NOTES = {
    AddResult.ADDED: "dm_start.clue_added",
    AddResult.DUPLICATE: "dm_start.clue_duplicate",
    AddResult.FULL: "dm_start.clue_full",
}


def accepts_panel_taps(game: Game) -> bool:
    """Whether the SETUP game is still where the panel was opened from: on
    the preview, and with an author to choose (never HARD MODE)."""
    return game.setup_step in _PANEL_STEPS and not game.hard_mode


def panel_screen(session: Session, game: Game, lang: str, note: str | None = None) -> Screen:
    """The panel: the mode, how full the list is, and its buttons, with
    `note` (what the last action did) above it."""
    count = len(author_list.entries(session, game.id))
    mode = ClueScreenshotMode(game.clue_screenshot_mode)
    text = i18n.t(
        "dm_start.clue_panel",
        lang,
        mode=clue_mode_name(mode, lang),
        count=count,
        limit=author_list.MAX_AUTHOR_SCREENSHOTS,
    )
    if note:
        text = f"{note}\n\n{text}"
    return text, clue_panel_keyboard(lang, mode, count)


def add_note(result: AddResult, lang: str) -> str:
    """What the reply says about an addition to the list."""
    return i18n.t(_ADD_NOTES[result], lang, limit=author_list.MAX_AUTHOR_SCREENSHOTS)
