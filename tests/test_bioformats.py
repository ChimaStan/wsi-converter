"""Unit tests for Bio-Formats metadata normalization."""

from pathlib import Path
from types import SimpleNamespace

from wsi_converter.backends.bioformats.backend import parse_showinf
from wsi_converter.backends.bioformats.runner import BioFormatsRunner
from wsi_converter.models import SeriesClassification
from wsi_converter.series import classify_series


def test_parse_showinf_series_without_assuming_fixed_indices() -> None:
    """Parse actual series IDs and classify label versus high-resolution data."""
    text = """
Series #0: label
  Width = 400
  Height = 300
Series #3: sample 20x
  Width = 12000
  Height = 9000
  SizeC = 3
"""
    series = classify_series(parse_showinf(text))

    assert [item.index for item in series] == [0, 3]
    assert series[0].classification == SeriesClassification.ASSOCIATED_IMAGE
    assert series[1].classification == SeriesClassification.WSI
    assert series[1].channels == 3


def test_parse_showinf_uses_series_index_when_header_only_has_image_count() -> None:
    """Avoid treating Bio-Formats' generic image-count header as a series name."""
    series = parse_showinf("""
Series #6: Image count = 1
  Width = 18032
  Height = 9148
""")

    assert series[0].index == 6
    assert series[0].name == "Series 6"


def test_parse_showinf_uses_ome_image_names_and_resolution_metadata() -> None:
    """Use logical names and pyramid counts exposed by the Bio-Formats reader."""
    text = '''
Series count = 2
Series #0 :
  Resolutions = 4
  Width = 10000
  Height = 8000
  Thumbnail series = false
Series #1 :
  Width = 500
  Height = 300
  Thumbnail series = true
<OME><Image ID="Image:0" Name="M-01_BF_01H&amp;E"><Pixels SizeX="10000" SizeY="8000"/></Image>
<Image ID="Image:1" Name="macro image"><Pixels SizeX="500" SizeY="300"/></Image></OME>
Reading series #1 metadata
Layer: Macro
'''

    series = classify_series(parse_showinf(text))

    assert series[0].name == "M-01_BF_01H&E"
    assert series[0].resolution_count == 4
    assert series[0].flat_index == 0
    assert series[0].classification == SeriesClassification.WSI
    assert series[1].name == "macro image"
    assert series[1].flat_index == 4
    assert series[1].classification == SeriesClassification.ASSOCIATED_IMAGE
    assert series[1].raw_metadata["Layer"] == "Macro"


def test_runner_only_requests_ome_xml_when_requested(tmp_path: Path, monkeypatch) -> None:
    """Keep output verification compact while allowing full source discovery."""
    executable = tmp_path / "showinf"
    executable.touch()
    source = tmp_path / "sample.ome.tiff"
    source.touch()
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("wsi_converter.backends.bioformats.runner.subprocess.run", fake_run)
    runner = BioFormatsRunner(showinf=executable)

    runner.inspect(source)
    runner.inspect(source, include_ome_xml=True)

    assert "-omexml" not in calls[0]
    assert calls[0][-1] == str(source)
    assert calls[1].count("-omexml") == 1


def test_runner_forces_bigtiff_without_enabling_compression(tmp_path: Path, monkeypatch) -> None:
    """Write large OME-TIFFs with 64-bit offsets and preserve uncompressed pixels."""
    executable = tmp_path / "bfconvert"
    executable.touch()
    source = tmp_path / "sample.vsi"
    source.touch()
    output = tmp_path / "converted.ome.tiff"
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("wsi_converter.backends.bioformats.runner.subprocess.run", fake_run)
    runner = BioFormatsRunner(bfconvert=executable)

    runner.convert(source, 13, output, preserve_pyramid=False)

    assert calls[0] == [
        str(executable), "-no-upgrade", "-bigtiff", "-series", "13", str(source), str(output)
    ]
    assert "-compression" not in calls[0]


def test_runner_passes_requested_compression_method(tmp_path: Path, monkeypatch) -> None:
    """Pass a requested codec directly to Bio-Formats without changing defaults."""
    executable = tmp_path / "bfconvert"
    executable.touch()
    source = tmp_path / "sample.vsi"
    source.touch()
    output = tmp_path / "converted.ome.tiff"
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("wsi_converter.backends.bioformats.runner.subprocess.run", fake_run)
    runner = BioFormatsRunner(bfconvert=executable)

    runner.convert(source, 13, output, preserve_pyramid=True, compression="LZW")

    assert calls[0] == [
        str(executable), "-no-upgrade", "-bigtiff", "-noflat", "-compression", "LZW",
        "-series", "13", str(source), str(output),
    ]


def test_runner_rejects_empty_compression_method_before_launch(tmp_path: Path, monkeypatch) -> None:
    """Reject a blank codec instead of silently falling back to uncompressed output."""
    import pytest

    from wsi_converter.exceptions import ConversionError

    executable = tmp_path / "bfconvert"
    executable.touch()
    source = tmp_path / "sample.vsi"
    source.touch()
    output = tmp_path / "converted.ome.tiff"
    monkeypatch.setattr(
        "wsi_converter.backends.bioformats.runner.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("bfconvert should not be launched"),
    )

    with pytest.raises(ConversionError, match="cannot be empty"):
        BioFormatsRunner(bfconvert=executable).convert(
            source, 13, output, compression="  "
        )
