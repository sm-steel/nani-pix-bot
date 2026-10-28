"""The /hex messages: the rules with the player picker under them, and the kind
picker for a chosen target. Each returns its text and keyboard."""

from datetime import timedelta

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.helpers.player_picker import PickEntry, View, entries, picker_keyboard
from nani_pix_bot.commands.hexes.common import ICONS, cast_data, kind_name, now, picker
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexRule
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.seasons import hexes
from nani_pix_bot.services.seasons.hex_targets import hex_targets


def status_line(session: Session, player_id: int, lang: str) -> str:
    player = session.get(Player, player_id)
    out = player is not None and player.hex_opt_out
    return i18n.t("hexes.status.out" if out else "hexes.status.in", lang)


def _rule_line(rule: HexRule, lang: str) -> str:
    effect = i18n.t(
        f"hex.kind.{rule.kind.value}.effect", lang, hours=rule.duration_hours, amount=rule.amount
    )
    return i18n.t(
        "hex.help.line",
        lang,
        icon=ICONS[rule.kind],
        name=kind_name(rule.kind.value, lang),
        price=rule.price,
        cooldown=duration(timedelta(minutes=rule.cooldown_minutes), lang),
        effect=effect,
    )


def _help(session: Session, opened: hexes.OpenHexes, caster_id: int, lang: str) -> str:
    lines = [i18n.t("hex.help.header", lang)]
    lines += [_rule_line(rule, lang) for rule in opened.run.hexes]
    return "\n".join([*lines, "", status_line(session, caster_id, lang)])


def _entries(session: Session, opened: hexes.OpenHexes, caster_id: int) -> list[PickEntry]:
    targets = hex_targets(session, opened.season, caster_id, now())
    return entries(session, [p.telegram_user_id for p in targets])


def start_view(session: Session, caster_id: int, page: int, lang: str) -> View:
    """/hex with no target: the season's hexes, then a page of players to hex."""
    opened = hexes.open_hexes(session)
    if opened is None:
        return i18n.t("hex.closed", lang), None
    help_text = _help(session, opened, caster_id, lang)
    found = _entries(session, opened, caster_id)
    if not found:
        return f"{help_text}\n\n{i18n.t('hex.no_targets', lang)}", None
    keyboard = picker_keyboard(found, page, picker(caster_id).data(), lang)
    return f"{help_text}\n\n{i18n.t('hex.pick_target', lang)}", keyboard


def _kind_button(caster_id: int, target_id: int, rule: HexRule, lang: str) -> InlineKeyboardButton:
    label = i18n.t(
        "hex.button",
        lang,
        icon=ICONS[rule.kind],
        name=kind_name(rule.kind.value, lang),
        price=rule.price,
    )
    return InlineKeyboardButton(label, callback_data=cast_data(caster_id, target_id, rule.kind))


def kind_view(session: Session, caster_id: int, target_id: int, lang: str) -> View:
    """One button per hex of the season for `target_id`, then ◀ Back and ✖ cancel."""
    opened = hexes.open_hexes(session)
    if opened is None:
        return i18n.t("hex.closed", lang), None
    rows = [[_kind_button(caster_id, target_id, rule, lang)] for rule in opened.run.hexes]
    back = picker(caster_id)
    rows.append(
        [
            InlineKeyboardButton(i18n.t("hex.back", lang), callback_data=back.page(0)),
            InlineKeyboardButton(i18n.t("hex.cancel", lang), callback_data=back.cancel()),
        ]
    )
    text = i18n.t("hex.pick", lang, target=players.display_name(session, target_id, unmasked=True))
    return text, InlineKeyboardMarkup(rows)
