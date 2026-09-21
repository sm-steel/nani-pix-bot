"""Env/.env loading — the only module that reads os.environ directly."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        msg = f"{name} is not set"
        raise RuntimeError(msg)
    return value


def database_url() -> str:
    return _require("DATABASE_URL")


@dataclass(frozen=True)
class Config:
    bot_token: str
    group_chat_id: int
    game_topic_id: int
    admin_user_ids: list[int]
    log_level: str
    telegram_proxy_url: str | None
    tmdb_read_access_token: str | None
    mal_client_id: str | None
    mal_client_secret: str | None
    mal_redirect_uri: str | None
    mal_token_encryption_key: str | None
    database_url: str


def load_config() -> Config:
    admin_ids_raw = os.environ.get("ADMIN_USER_IDS", "")
    admin_user_ids = [int(chunk) for chunk in admin_ids_raw.split(",") if chunk.strip()]
    return Config(
        bot_token=_require("BOT_TOKEN"),
        group_chat_id=int(_require("GROUP_CHAT_ID")),
        game_topic_id=int(_require("GAME_TOPIC_ID")),
        admin_user_ids=admin_user_ids,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        telegram_proxy_url=os.environ.get("TELEGRAM_PROXY_URL") or None,
        # Optional, unlike every other required field above — a fresh
        # clone with no TMDB key can still run everything except the
        # TMDB identification/screenshot method (which degrades to the
        # existing "service down" messaging rather than crashing).
        tmdb_read_access_token=os.environ.get("TMDB_READ_ACCESS_TOKEN") or None,
        # Optional, and optional together — a fresh clone with none of
        # these set simply never shows the "My MAL list" identification
        # method (same degrade-gracefully precedent as TMDB above).
        # MAL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key (44
        # url-safe-base64 chars) once MAL_CLIENT_ID is set — see
        # services/security/token_crypto.py, which validates this at
        # import/construction time so a bad key is caught at startup,
        # not on first write.
        mal_client_id=os.environ.get("MAL_CLIENT_ID") or None,
        mal_client_secret=os.environ.get("MAL_CLIENT_SECRET") or None,
        mal_redirect_uri=os.environ.get("MAL_REDIRECT_URI") or None,
        mal_token_encryption_key=os.environ.get("MAL_TOKEN_ENCRYPTION_KEY") or None,
        database_url=database_url(),
    )
