"""Simple dict/JSON i18n — see CLAUDE.md's coding practices. Deliberately
not gettext/Babel/python-i18n: two languages and a bot's worth of strings
don't need that ceremony."""

import json
import secrets
from functools import cache
from pathlib import Path
from typing import Any

from loguru import logger

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
_VARIATIONS_DIR = _LOCALES_DIR / "variations"
DEFAULT_LANGUAGE = "en"


@cache
def _load(lang: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def _load_variations(lang: str) -> dict[str, list[str]]:
    path = _VARIATIONS_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, lang: str, **kwargs: Any) -> str:
    """Look up `key` in `lang`'s catalog and `.format(**kwargs)` it.

    Falls back to English if missing in another language; if it's missing
    even there, logs an error and returns the raw key rather than raising
    — a bad translation shouldn't crash a live bot.

    If `key` also has extra phrasings in `locales/variations/{lang}.json`,
    one is picked at random alongside the canonical wording each call —
    so a message sent many times in one game (e.g. a wrong guess) doesn't
    always read identically.
    """
    lang = lang.lower()
    template = _load(lang).get(key)
    if template is None:
        if lang != DEFAULT_LANGUAGE:
            logger.warning(
                "missing i18n key {key!r} for lang={language!r}, falling back to {fallback!r}",
                key=key,
                language=lang,
                fallback=DEFAULT_LANGUAGE,
            )
            return t(key, DEFAULT_LANGUAGE, **kwargs)
        logger.error(
            "missing i18n key {key!r} in default language {language!r}",
            key=key,
            language=DEFAULT_LANGUAGE,
        )
        return key
    pool = [template, *_load_variations(lang).get(key, [])]
    return secrets.choice(pool).format(**kwargs)


def plural(n: int, lang: str) -> str:
    """Which plural form a count takes: "one", "few" or "many". Russian
    follows its own rule (1 очко, 2 очка, 5 очков, 11 очков, 21 очко);
    English only has "one" and "many", so a locale key gets all three
    forms in both languages and English's "few" simply goes unused."""
    if lang.lower() != "ru":
        return "one" if n == 1 else "many"
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


_ENGLISH_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n: int, lang: str) -> str:
    """'1st'/'2nd'/'3rd'/'11th' in English. Other languages get the bare
    number: Russian puts its ending in the template itself."""
    if lang.lower() != "en":
        return str(n)
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{_ENGLISH_ORDINAL_SUFFIX.get(n % 10, 'th')}"
