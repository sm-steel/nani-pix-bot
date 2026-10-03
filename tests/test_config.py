import pytest

from nani_pix_bot import config


def test_database_url_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    assert config.database_url() == "sqlite:///:memory:"


def test_database_url_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.database_url()


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("GROUP_CHAT_ID", "-100555")
    monkeypatch.setenv("GAME_TOPIC_ID", "7")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)


def test_load_config_reads_all_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)

    loaded = config.load_config()

    assert loaded.bot_token == "test-token"  # noqa: S105 - test fixture, not a real token
    assert loaded.group_chat_id == -100555
    assert loaded.game_topic_id == 7
    assert loaded.database_url == "sqlite:///:memory:"
    assert loaded.admin_user_ids == []
    assert loaded.log_level == "INFO"
    assert loaded.telegram_proxy_url is None


def test_load_config_parses_admin_user_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("ADMIN_USER_IDS", "1, 2,3")

    loaded = config.load_config()

    assert loaded.admin_user_ids == [1, 2, 3]


def test_load_config_reads_optional_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://user:pass@proxyhost:8888")

    loaded = config.load_config()

    assert loaded.log_level == "DEBUG"
    assert loaded.telegram_proxy_url == "http://user:pass@proxyhost:8888"


def test_load_config_raises_when_bot_token_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        config.load_config()


def test_load_config_leaves_mal_fields_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("MAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("MAL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MAL_REDIRECT_URI", raising=False)
    monkeypatch.delenv("MAL_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)

    config_obj = config.load_config()

    assert config_obj.mal_client_id is None
    assert config_obj.mal_client_secret is None
    assert config_obj.mal_redirect_uri is None
    assert config_obj.mal_token_encryption_key is None


def test_load_config_reads_mal_fields_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MAL_CLIENT_ID", "cid")
    monkeypatch.setenv("MAL_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MAL_REDIRECT_URI", "https://example.github.io/mal-callback.html")
    monkeypatch.setenv("MAL_TOKEN_ENCRYPTION_KEY", "keykeykeykeykeykeykeykeykeykeykeykeykey=")

    config_obj = config.load_config()

    assert config_obj.mal_client_id == "cid"
    assert config_obj.mal_client_secret == "csecret"  # noqa: S105 - test fixture, not a real secret
    assert config_obj.mal_redirect_uri == "https://example.github.io/mal-callback.html"
    assert config_obj.mal_token_encryption_key == "keykeykeykeykeykeykeykeykeykeykeykeykey="  # noqa: S105 - test fixture, not a real key


def _set_all_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "bot-credential-value")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://appuser:db-pw-value@mariadb:3306/appdb")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://proxyuser:proxy-pw-value@proxyhost:8888")
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "tmdb-credential-value")
    monkeypatch.setenv("MAL_CLIENT_SECRET", "mal-client-credential-value")
    monkeypatch.setenv("MAL_TOKEN_ENCRYPTION_KEY", "mal-fernet-credential-value")
    monkeypatch.setenv("MARIADB_PASSWORD", "mariadb-pw-value")
    monkeypatch.setenv("MARIADB_ROOT_PASSWORD", "mariadb-root-pw-value")


def test_secret_values_collects_every_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #184: everything the logging filter must mask, by exact value."""
    _set_all_secrets(monkeypatch)

    secrets = config.load_config().secret_values()

    assert set(secrets) == {
        "bot-credential-value",
        "db-pw-value",
        "proxy-pw-value",
        "tmdb-credential-value",
        "mal-client-credential-value",
        "mal-fernet-credential-value",
        "mariadb-pw-value",
        "mariadb-root-pw-value",
    }


def test_secret_values_leaves_out_non_secret_parts_of_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_secrets(monkeypatch)

    secrets = config.load_config().secret_values()

    for not_secret in ("appuser", "proxyuser", "mariadb", "proxyhost", "appdb", "-100555"):
        assert not any(not_secret == s for s in secrets)


def test_secret_values_skips_unset_and_empty_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string in the list would "match" at every position."""
    _set_required_env(monkeypatch)
    for name in (
        "TMDB_READ_ACCESS_TOKEN",
        "MAL_CLIENT_SECRET",
        "MAL_TOKEN_ENCRYPTION_KEY",
        "MARIADB_ROOT_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARIADB_PASSWORD", "")

    secrets = config.load_config().secret_values()

    assert secrets == ["test-token"]
