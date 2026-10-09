"""The hand-written release notes /version shows (see docs/release-notes.md
for the rules). Two markdown files per language, packaged with the bot:
`release-notes-<lang>.md`, the notes of the running (or upcoming) release
with no heading of its own, and `previous-releases-<lang>.md`, older
releases newest first, each under a `## vX.Y.Z · YYYY-MM-DD` heading.
File reading and parsing only, no Telegram."""

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

NOTES_DIR = Path(__file__).resolve().parent.parent / "release_notes"
MAX_PREVIOUS = 4
_HEADING = "## "
_DATE_SEPARATOR = " · "


@dataclass(frozen=True)
class Release:
    version: str | None  # None for the running release: /version knows it
    date: str | None
    markdown: str


def _read(path: Path) -> str:
    """A missing or broken file reads as empty, so /version still answers
    ("Nothing noted") instead of failing."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("release notes {path} not found", path=str(path))
    except (OSError, UnicodeDecodeError):
        logger.opt(exception=True).error("release notes {path} could not be read", path=str(path))
    return ""


def _release(chunk: str) -> Release:
    """One `## ` section, its heading marker already split off."""
    title, _, body = chunk.partition("\n")
    version, _, date = title.partition(_DATE_SEPARATOR)
    return Release(version.strip().removeprefix("v"), date.strip() or None, body.strip())


def _parse_previous(text: str) -> list[Release]:
    # Text before the first heading (chunk 0) belongs to no release.
    chunks = ("\n" + text).split("\n" + _HEADING)[1:]
    return [_release(chunk) for chunk in chunks]


def load(lang: str, directory: Path | None = None) -> list[Release]:
    """Page 0 is the current notes (possibly empty), then up to
    MAX_PREVIOUS older releases, newest first. `directory` defaults to
    NOTES_DIR, looked up per call."""
    directory = NOTES_DIR if directory is None else directory
    current = Release(None, None, _read(directory / f"release-notes-{lang}.md").strip())
    previous = _parse_previous(_read(directory / f"previous-releases-{lang}.md"))
    return [current, *previous[:MAX_PREVIOUS]]
