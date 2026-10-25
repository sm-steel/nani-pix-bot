"""What the /hex and /hexes modules share: the callback data, the hex icons and
names, the DM-or-game-topic scope, and the clock."""

from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.player_picker import Picker
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n, settings

PREFIX = "hex:"

ICONS = {
    HexKind.NOPPERABO: "👤",
    HexKind.CHOCHIN: "🏮",
    HexKind.KAPPA: "🥒",
    HexKind.KITSUNE: "🦊",
}


def now() -> datetime:
    return datetime.now(UTC)


def kind_name(kind: str, lang: str) -> str:
    return i18n.t(f"hex.kind.{kind}.name", lang)


def parse_kind(text: str) -> HexKind | None:
    try:
        return HexKind(text.lower())
    except ValueError:
        return None


def in_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """A DM, or the game topic; like /tip, anywhere else is ignored."""
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    return in_topic or is_private_chat(update)


def language(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


# --- callback data: every tap names the caster, so only they can use it ---


def picker(caster_id: int) -> Picker:
    return Picker(PREFIX, caster_id)


def page_data(caster_id: int, page: int) -> str:
    return picker(caster_id).page(page)


def cast_data(caster_id: int, target_id: int, kind: HexKind) -> str:
    return f"{PREFIX}{caster_id}:{target_id}:{kind.value}"
