"""The clue screenshot panel's screen (issue #452), shared by the panel
itself (clue_panel.py) and by the gallery that adds to the author's list
from a source (clue_gallery.py), which re-shows it after a pick."""

from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup

from nani_pix_bot.commands.dm_start.keyboards import clue_mode_name, clue_panel_keyboard
from nani_pix_bot.models.enums import ClueScreenshotMode
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.clues.author_list import AddResult

Screen = tuple[str, InlineKeyboardMarkup | None]

_ADD_NOTES = {
    AddResult.ADDED: "dm_start.clue_added",
    AddResult.DUPLICATE: "dm_start.clue_duplicate",
    AddResult.FULL: "dm_start.clue_full",
}


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
