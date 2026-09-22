"""Per-player MyAnimeList OAuth linking — see
docs/superpowers/specs/2026-09-21-mal-account-linking-design.md.

MalCredentials.access_token/refresh_token are stored ENCRYPTED (see
services/security/token_crypto.py) — services/mal_link.py is the only
code that should read/write these columns directly; everything else
goes through its encrypt-on-write/decrypt-on-read functions."""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

MAL_USERNAME_LENGTH = 64
# A real MyAnimeList OAuth token, once Fernet-encrypted, exceeded the
# original 512-char guess in production (issue #185 — "Data too long
# for column 'access_token'" against a real linked account; the real
# token observed was a ~1010-char opaque string, Fernet-encrypting to
# ~1444 chars). 4096 gives roughly 2.8x headroom over that observed
# value — chosen deliberately over an even larger guess (8192 was
# tried first and rejected outright by MariaDB: two VARCHAR(8192)
# utf8mb4 columns alone exceed InnoDB's 65535-byte row-size ceiling,
# not counting BLOB/TEXT columns).
ENCRYPTED_TOKEN_LENGTH = 4096
PKCE_TOKEN_LENGTH = 128


class MalCredentials(Base):
    __tablename__ = "mal_credentials"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    access_token: Mapped[str] = mapped_column(String(ENCRYPTED_TOKEN_LENGTH))
    refresh_token: Mapped[str] = mapped_column(String(ENCRYPTED_TOKEN_LENGTH))
    expires_at: Mapped[datetime]
    mal_username: Mapped[str | None] = mapped_column(String(MAL_USERNAME_LENGTH), default=None)
    linked_at: Mapped[datetime]


class PendingMalLink(Base):
    """A live /linkmal attempt's PKCE state, between generating the
    authorize URL and the player pasting back the code — DB-backed
    (not bot_data/in-memory) so a restart mid-link doesn't silently
    lose it, matching this codebase's established setup-flow-state
    convention (issue #11). One row per player — a second /linkmal
    while one is pending overwrites this row in place (see
    services/mal_link.py's upsert_pending_link)."""

    __tablename__ = "pending_mal_link"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(PKCE_TOKEN_LENGTH))
    code_verifier: Mapped[str] = mapped_column(String(PKCE_TOKEN_LENGTH))
    created_at: Mapped[datetime]
