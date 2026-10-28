"""The /tip menus: the player picker, and the amount buttons after a pick
made without an amount. Callback data carries the amount (0 = not chosen yet):

- `tip:<tipper>:<amount>:p:<page>`: a page of the player picker.
- `tip:<tipper>:<amount>:t:<recipient>`: with an amount, tip it; with 0, the
  amount buttons, each of which is the same tap with its amount.
- `tip:<tipper>:<amount>:x`: cancel."""

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.helpers.player_picker import (
    Picker,
    PickerSpec,
    View,
    entries,
    picker_keyboard,
)
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.economy import tips

PREFIX = "tip:"
SPEC = PickerSpec(PREFIX, "/tip", state_parts=1)


def picker(tipper_id: int, amount: int) -> Picker:
    return Picker(PREFIX, tipper_id, (str(amount),))


def players_view(session: Session, at: Picker, bot_id: int, page: int) -> View:
    """Every known player but the tipper and Nani Pix herself, most active first."""
    lang = settings.get_language(session)
    excluded = {at.opener_id, bot_id}
    candidates = [pid for pid in players.known_ids(session) if pid not in excluded]
    found = entries(session, players.by_activity(session, candidates, None))
    if not found:
        return i18n.t("tip.no_one", lang), None
    amount = int(at.state[0])
    text = (
        i18n.t("tip.pick_player_amount", lang, amount=amount)
        if amount
        else i18n.t("tip.pick_player", lang)
    )
    return text, picker_keyboard(found, page, at.data(), lang)


def amount_view(session: Session, tipper_id: int, recipient_id: int) -> View | None:
    """The TIP_AMOUNTS the tipper can afford, as buttons; None when none is."""
    lang = settings.get_language(session)
    tipper = session.get(Player, tipper_id)
    amounts = tips.affordable_amounts(0 if tipper is None else tipper.currency)
    if not amounts:
        return None
    buttons = [
        InlineKeyboardButton(
            i18n.t("tip.amount_button", lang, amount=amount),
            callback_data=picker(tipper_id, amount).target(recipient_id),
        )
        for amount in amounts
    ]
    cancel = InlineKeyboardButton(
        i18n.t("player_picker.cancel", lang), callback_data=picker(tipper_id, 0).cancel()
    )
    recipient = players.display_name(session, recipient_id, unmasked=True)
    text = i18n.t("tip.pick_amount", lang, recipient=recipient)
    return text, InlineKeyboardMarkup([buttons, [cancel]])
