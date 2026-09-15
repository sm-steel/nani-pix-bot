from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot.commands.dm_start._shared import _search_and_build_keyboard


async def test_search_and_build_keyboard_returns_results_and_the_built_keyboard() -> None:
    client = MagicMock()
    keyboard = MagicMock()
    search_fn = AsyncMock(return_value=["a", "b"])
    keyboard_fn = MagicMock(return_value=keyboard)

    results, built = await _search_and_build_keyboard(client, "frieren", search_fn, keyboard_fn)

    search_fn.assert_awaited_once_with(client, "frieren")
    keyboard_fn.assert_called_once_with(["a", "b"])
    assert results == ["a", "b"]
    assert built is keyboard


async def test_search_and_build_keyboard_returns_no_keyboard_for_empty_results() -> None:
    client = MagicMock()
    search_fn = AsyncMock(return_value=[])
    keyboard_fn = MagicMock()

    results, built = await _search_and_build_keyboard(client, "zzzz", search_fn, keyboard_fn)

    keyboard_fn.assert_not_called()
    assert results == []
    assert built is None


async def test_search_and_build_keyboard_propagates_a_search_failure() -> None:
    client = MagicMock()
    search_fn = AsyncMock(side_effect=RuntimeError("boom"))
    keyboard_fn = MagicMock()

    with pytest.raises(RuntimeError, match="boom"):
        await _search_and_build_keyboard(client, "frieren", search_fn, keyboard_fn)

    keyboard_fn.assert_not_called()
