"""Env/.env loading — the only module that reads os.environ directly."""

import os

from dotenv import load_dotenv

load_dotenv()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url
