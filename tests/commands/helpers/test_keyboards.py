from nani_pix_bot.commands.helpers.keyboards import (
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
    stop_confirm_keyboard,
)


def test_stop_confirm_keyboard_has_confirm_and_cancel_buttons() -> None:
    markup = stop_confirm_keyboard(lang="en")

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [STOP_CONFIRM_CALLBACK_DATA, STOP_CANCEL_CALLBACK_DATA]


def test_stop_confirm_keyboard_labels_are_translated() -> None:
    markup_en = stop_confirm_keyboard(lang="en")
    markup_ru = stop_confirm_keyboard(lang="ru")

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en != labels_ru
