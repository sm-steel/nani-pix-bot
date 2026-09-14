"""stop_confirm_keyboard() — shared across commands/game_flow/stop.py and
commands/stageconfig.py (offered when a config edit is blocked by a
running game), so it lives here in helpers/ rather than in either
package specifically. Every other keyboard builder is specific to one
flow: commands/dm_start/keyboards.py for the DM setup flow."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services import i18n

STOP_CONFIRM_CALLBACK_DATA = "stop:confirm"
STOP_REVEAL_CALLBACK_DATA = "stop:reveal"
STOP_CANCEL_CALLBACK_DATA = "stop:cancel"


def stop_confirm_keyboard(lang: str, *, can_reveal: bool = False) -> InlineKeyboardMarkup:
    """Yes/No confirmation shown by `/stop` before actually deleting a
    running game — see MECHANICS.md's "Stopping a game" section.

    `can_reveal` adds the third "stop and reveal" button, which also
    posts the round's answer to the group topic. Callers pass it only
    for an ACTIVE game with its image still stored: a SETUP game has
    never posted anything to the group, so there's no answer anyone is
    waiting on. The reveal button gets its own row — its label is the
    longest of the three and would squeeze the others in a shared one."""
    yes = InlineKeyboardButton(
        i18n.t("keyboards.stop_confirm", lang), callback_data=STOP_CONFIRM_CALLBACK_DATA
    )
    no = InlineKeyboardButton(
        i18n.t("keyboards.stop_cancel", lang), callback_data=STOP_CANCEL_CALLBACK_DATA
    )
    if not can_reveal:
        return InlineKeyboardMarkup([[yes, no]])

    reveal = InlineKeyboardButton(
        i18n.t("keyboards.stop_reveal", lang), callback_data=STOP_REVEAL_CALLBACK_DATA
    )
    return InlineKeyboardMarkup([[yes], [reveal], [no]])
