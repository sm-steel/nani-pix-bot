"""The running build's version, for /version (commands/version.py) and the
`version` field on every log record (app.py)."""

from importlib.metadata import version as _installed_version

_DISTRIBUTION_NAME = "nani-pix-bot"


def installed_version() -> str:
    """The version stamped into this build's package metadata at release
    time (see pyproject.toml's `[tool.semantic_release] version_toml`).
    Reads back "0.1.0" for an unstamped local dev checkout."""
    return _installed_version(_DISTRIBUTION_NAME)
