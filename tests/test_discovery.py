"""Unit tests for WSI candidate discovery."""

from pathlib import Path

from wsi_converter.discovery import discover


def test_discovery_is_sorted_deduplicated_and_extension_scoped(tmp_path: Path) -> None:
    """Directory candidates should be filtered, sorted, and de-duplicated."""
    (tmp_path / "z.vsi").touch()
    (tmp_path / "a.ome.tiff").touch()
    (tmp_path / "notes.txt").touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.svs").touch()

    result = discover([tmp_path, tmp_path / "z.vsi"], recursive=True)

    assert [path.name for path in result.files] == ["a.ome.tiff", "b.svs", "z.vsi"]
    assert result.inaccessible == []
