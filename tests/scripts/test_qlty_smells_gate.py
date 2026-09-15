import subprocess
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from scripts import qlty_smells_gate


@pytest.fixture
def mock_run_qlty() -> Iterator[type[subprocess.CompletedProcess[str]]]:
    """Patch `_run_qlty` so each test controls the exact CompletedProcess it returns."""
    with patch.object(qlty_smells_gate, "_run_qlty") as mock:
        yield mock


def test_clean_tree_passes(mock_run_qlty, capsys: pytest.CaptureFixture[str]) -> None:
    mock_run_qlty.return_value = subprocess.CompletedProcess(
        args=["qlty"], returncode=0, stdout="", stderr=""
    )

    assert qlty_smells_gate.main() == 0
    assert capsys.readouterr().out == ""


def test_findings_present_fails_and_prints_them(
    mock_run_qlty, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_run_qlty.return_value = subprocess.CompletedProcess(
        args=["qlty"], returncode=0, stdout="<finding text>", stderr=""
    )

    assert qlty_smells_gate.main() == 1
    assert "<finding text>" in capsys.readouterr().out


def test_qlty_execution_failure_fails_and_prints_stderr(
    mock_run_qlty, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_run_qlty.return_value = subprocess.CompletedProcess(
        args=["qlty"],
        returncode=1,
        stdout="",
        stderr="qlty not found on PATH — see CLAUDE.md's Tooling section.",
    )

    assert qlty_smells_gate.main() == 1
    assert "qlty not found on PATH" in capsys.readouterr().out
