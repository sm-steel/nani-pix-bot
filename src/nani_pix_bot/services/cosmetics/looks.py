"""What a valid item looks like, per slot (seasons spec §3)."""

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from nani_pix_bot.models.enums import CosmeticSlot, RevealEffect
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import mascot
from nani_pix_bot.services.cosmetics.frames import FRAMES
from nani_pix_bot.services.reveal.overlays import WIN_OVERLAYS

SEASON_ART_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "seasons"
MAX_FLAIR_CHARS = 8
MAX_COSTUME_KEY = 16
_COSTUME_KEY = re.compile(rf"^[a-z][a-z_]{{0,{MAX_COSTUME_KEY - 1}}}$")
# A title item id rides in /title callback data ("title:i:" + id, Telegram caps it
# at 64 bytes) and in Player.title_key ("item:" + id, String(96)); the callback is
# the tighter bound.
MAX_TITLE_ID_BYTES = 56

# Telegram's allowed reaction emoji (Bot API ReactionTypeEmoji).
ALLOWED_REACTIONS = frozenset(
    {
        "❤",
        "👍",
        "👎",
        "🔥",
        "🥰",
        "👏",
        "😁",
        "🤔",
        "🤯",
        "😱",
        "🤬",
        "😢",
        "🎉",
        "🤩",
        "🤮",
        "💩",
        "🙏",
        "👌",
        "🕊",
        "🤡",
        "🥱",
        "🥴",
        "😍",
        "🐳",
        "❤\u200d🔥",
        "🌚",
        "🌭",
        "💯",
        "🤣",
        "⚡",
        "🍌",
        "🏆",
        "💔",
        "🤨",
        "😐",
        "🍓",
        "🍾",
        "💋",
        "🖕",
        "😈",
        "😴",
        "😭",
        "🤓",
        "👻",
        "👨\u200d💻",
        "👀",
        "🎃",
        "🙈",
        "😇",
        "😨",
        "🤝",
        "✍",
        "🤗",
        "🫡",
        "🎅",
        "🎄",
        "☃",
        "💅",
        "🤪",
        "🗿",
        "🆒",
        "💘",
        "🙉",
        "🦄",
        "😘",
        "💊",
        "🙊",
        "😎",
        "👾",
        "🤷\u200d♂",
        "🤷",
        "🤷\u200d♀",
        "😡",
    }
)


def banner_path(run_id: str, file_name: str) -> Path:
    return SEASON_ART_DIR / run_id / "banners" / file_name


def _flair_problem(resolved: ResolvedItem) -> str | None:
    look = resolved.item.look
    bad = not look or len(look) > MAX_FLAIR_CHARS or any(c.isalnum() for c in look)
    return "flair must be a short emoji" if bad else None


def _reaction_problem(resolved: ResolvedItem) -> str | None:
    look = resolved.item.look
    return None if look in ALLOWED_REACTIONS else f"{look!r} is not an allowed reaction"


def _frame_problem(resolved: ResolvedItem) -> str | None:
    look = resolved.item.look
    return None if look in FRAMES else f"unknown frame {look!r}"


def _transition_problem(resolved: ResolvedItem) -> str | None:
    look = resolved.item.look
    return None if look in RevealEffect.__members__ else f"unknown reveal effect {look!r}"


def _overlay_problem(resolved: ResolvedItem) -> str | None:
    look = resolved.item.look
    return None if look in WIN_OVERLAYS else f"unknown win overlay {look!r}"


def _banner_problem(resolved: ResolvedItem) -> str | None:
    path = banner_path(resolved.run.run_id, resolved.item.look)
    return None if path.is_file() else f"missing banner file {path}"


def _title_problem(resolved: ResolvedItem) -> str | None:
    size = len(resolved.item_id.encode())
    if size <= MAX_TITLE_ID_BYTES:
        return None
    return f"title id too long for /title callbacks ({size} bytes > {MAX_TITLE_ID_BYTES})"


def costume_problems(key: str) -> list[str]:
    """Why `key` is not a usable Nani Pix costume: a badly shaped key, or one
    problem per pose file that is absent."""
    if not _COSTUME_KEY.fullmatch(key):
        return [f"bad costume key {key!r} (lowercase letters and _, at most {MAX_COSTUME_KEY})"]
    paths = (mascot.pose_path(pose, key) for pose in mascot.Pose)
    return [f"missing costume pose file {path}" for path in paths if not path.is_file()]


def _costume_problem(resolved: ResolvedItem) -> str | None:
    return next(iter(costume_problems(resolved.item.look)), None)


# A slot with no entry has no look to check.
_LOOK_CHECKS: Mapping[CosmeticSlot, Callable[[ResolvedItem], str | None]] = MappingProxyType(
    {
        CosmeticSlot.NAME_FLAIR: _flair_problem,
        CosmeticSlot.GUESS_REACTION: _reaction_problem,
        CosmeticSlot.HOST_FRAME: _frame_problem,
        CosmeticSlot.HOST_TRANSITION: _transition_problem,
        CosmeticSlot.WIN_EFFECT: _overlay_problem,
        CosmeticSlot.PROFILE_BANNER: _banner_problem,
        CosmeticSlot.TITLE: _title_problem,
        CosmeticSlot.MASCOT_COSTUME: _costume_problem,
    }
)


def _look_problem(resolved: ResolvedItem) -> str | None:
    check = _LOOK_CHECKS.get(resolved.item.slot)
    return check(resolved) if check else None


def problems(resolved: ResolvedItem) -> list[str]:
    item = resolved.item
    found = []
    if not item.key.startswith(f"{item.slot.value}."):
        found.append(f"{resolved.item_id}: key must start with {item.slot.value}.")
    found += [
        f"{resolved.item_id}: missing {lang} name"
        for lang in ("EN", "RU")
        if lang not in item.names
    ]
    look = _look_problem(resolved)
    if look is not None:
        found.append(f"{resolved.item_id}: {look}")
    return found
