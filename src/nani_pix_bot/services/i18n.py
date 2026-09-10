"""Simple dict/JSON i18n — see CLAUDE.md's coding practices. Deliberately
not gettext/Babel/python-i18n: two languages and a bot's worth of strings
don't need that ceremony."""

import json
from functools import cache
from pathlib import Path
from typing import Any

from loguru import logger

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANGUAGE = "en"


@cache
def _load(lang: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, lang: str, **kwargs: Any) -> str:
    """Look up `key` in `lang`'s catalog and `.format(**kwargs)` it.

    Falls back to English if missing in another language; if it's missing
    even there, logs an error and returns the raw key rather than raising
    — a bad translation shouldn't crash a live bot.
    """
    lang = lang.lower()
    template = _load(lang).get(key)
    if template is None:
        if lang != DEFAULT_LANGUAGE:
            logger.warning(
                "Missing i18n key {!r} for lang={!r}, falling back to {!r}",
                key,
                lang,
                DEFAULT_LANGUAGE,
            )
            return t(key, DEFAULT_LANGUAGE, **kwargs)
        logger.error("Missing i18n key {!r} in default language {!r}", key, DEFAULT_LANGUAGE)
        return key
    return template.format(**kwargs)
