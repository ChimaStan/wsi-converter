"""Unit tests for read-only environment and executable resolution helpers."""

from pathlib import Path

from wsi_converter.backends.bioformats.runner import BioFormatsRunner


def test_bioformats_home_resolves_tools_without_installing(tmp_path: Path) -> None:
    """Tool lookup should use the supplied installation root without mutation."""
    executable = tmp_path / "bin" / "showinf"
    executable.parent.mkdir()
    executable.write_text("external tool placeholder", encoding="utf-8")

    resolved = BioFormatsRunner.resolve_executable(None, "showinf", tmp_path)

    assert Path(resolved) == executable.resolve()
