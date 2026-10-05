"""Command-line smoke tests for subcommands with shared options."""

from pathlib import Path

import pytest

from wsi_converter.cli import main


def test_verify_runs_without_check_only_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify handles its parser options without accessing check-only fields."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    assert main(["verify", str(empty_dir)]) == 0
    assert "Files scanned:             0" in capsys.readouterr().out
