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
