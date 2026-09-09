def test_importing_models_package_registers_every_table() -> None:
    """Alembic's autogenerate walks Base.metadata — nothing gets missed only
    because some other module happened to import it first."""
    import nani_pix_bot.models as models

    assert {"players", "games", "turn_state"} <= set(models.Base.metadata.tables)
