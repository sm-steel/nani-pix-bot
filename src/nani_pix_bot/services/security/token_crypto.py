"""Symmetric encryption for the two secret columns in
models/mal_link.py's MalCredentials (access_token, refresh_token) —
scoped narrowly to those two fields, not a project-wide encryption
layer. See docs/superpowers/specs/2026-09-21-mal-account-linking-design.md
for why: a leaked MAL refresh token grants a renewable window onto a
real player's personal account, unlike TMDB's plaintext-stored
read-only catalog token."""

from cryptography.fernet import Fernet


def encrypt(key: str, plaintext: str) -> str:
    """`plaintext` encrypted with `key` (a Fernet key — 44 url-safe-base64
    characters, e.g. from `Fernet.generate_key()`). Returns a new
    ciphertext string each call even for the same plaintext (Fernet
    embeds a random IV and a timestamp) — this is expected, not a bug,
    and callers must not assume determinism."""
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def decrypt(key: str, ciphertext: str) -> str:
    """The original plaintext, or raises `cryptography.fernet.InvalidToken`
    if `key` doesn't match the key `ciphertext` was encrypted with, or
    `ciphertext` isn't a well-formed Fernet token at all. Callers must
    let this propagate (or catch it explicitly) rather than swallowing
    it — a wrong/rotated key must fail closed, never silently return
    garbage as if it were a valid token."""
    return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()
