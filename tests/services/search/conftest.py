from collections.abc import Iterator

import pytest
from loguru import logger


@pytest.fixture
def records() -> Iterator[list[tuple[str, str]]]:
    """Every log record emitted while the test runs, as (level, message).

    Shared by the parsing tests and the provider tests: a skip is only
    half-useful if it happens silently, so both levels of the suite check
    that a malformation leaves a WARNING naming the parser behind it."""
    captured: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda message: captured.append((message.record["level"].name, message.record["message"])),
        level="DEBUG",
    )
    yield captured
    logger.remove(sink_id)
