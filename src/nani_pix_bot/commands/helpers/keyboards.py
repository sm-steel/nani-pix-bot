"""stop_confirm_keyboard() — shared across commands/game_flow/stop.py and
commands/stageconfig.py (offered when a config edit is blocked by a
running game), so it lives here in helpers/ rather than in either
package specifically. Every other keyboard builder is specific to one
flow: commands/dm_start/keyboards.py for the DM setup flow."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services import i18n

STOP_CONFIRM_CALLBACK_DATA = "stop:confirm"
STOP_CANCEL_CALLBACK_DATA = "stop:cancel"


def stop_confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Yes/No confirmation shown by `/stop` before actually deleting a
    running game — see MECHANICS.md's "Stopping a game" section."""
    yes = InlineKeyboardButton(
        i18n.t("keyboards.stop_confirm", lang), callback_data=STOP_CONFIRM_CALLBACK_DATA
    )
    no = InlineKeyboardButton(
        i18n.t("keyboards.stop_cancel", lang), callback_data=STOP_CANCEL_CALLBACK_DATA
    )
    return InlineKeyboardMarkup([[yes, no]])
