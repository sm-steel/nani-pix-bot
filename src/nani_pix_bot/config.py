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
        database_url=database_url(),
    )
