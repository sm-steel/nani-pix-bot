"""Optional anime-styled card backgrounds (spec §7): image files the operator
drops into `assets/backgrounds/<slot>/`. Pure file lookup, no Telegram."""

from pathlib import Path

from nani_pix_bot.models.enums import Rarity

BACKGROUND_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "backgrounds"
_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
SEASON_ART_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "seasons"


def unlock_slot(rarity: Rarity) -> str:
    return f"unlock/{rarity.value}"


def podium_slot(period_type: str) -> str:
    return f"podium/{period_type}"


def load_background(slot: str, seed: int) -> bytes | None:
    """The bytes of one image of the slot, picked by `seed` among the files
    sorted by name (so a player always gets the same one); None if none."""
    folder = BACKGROUND_DIR / slot
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in _SUFFIXES)
    if not files:
        return None
    return files[seed % len(files)].read_bytes()


def season_background(run_id: str) -> bytes | None:
    """The run's committed key visual (seasons spec §6), or None before its art lands."""
    for suffix in sorted(_SUFFIXES):
        path = SEASON_ART_DIR / run_id / f"key_visual{suffix}"
        if path.is_file():
            return path.read_bytes()
    return None
