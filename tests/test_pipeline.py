"""Unit tests for multi-image conversion and independent output validation."""

import errno
from pathlib import Path
from typing import cast

from wsi_converter.backends.base import WSIBackend
from wsi_converter.models import (
    AssessmentStatus,
    ConversionResult,
    OpenSlideAssessment,
    SeriesClassification,
    SeriesInfo,
)
from wsi_converter.openslide import OpenSlideChecker
from wsi_converter.pipeline import (
    ConversionPipeline,
    _output_path,
    _publish_verified,
    _verify_bioformats_output,
)


class Checker:
    """Fixed compatible checker used to exercise source and output branches."""

    def check(self, path: Path, _requirements=None) -> OpenSlideAssessment:
        return OpenSlideAssessment(
            path=path, status=AssessmentStatus.COMPATIBLE,
            dimensions=(10000, 8000), level_count=3,
        )


class FakeBackend:
    """Small deterministic backend with flattened and logical inventories."""

    def __init__(self, *, fail_primary: bool = False) -> None:
        self.fail_primary = fail_primary
        self.converted: SeriesInfo | None = None
        self.calls: list[tuple[int, bool]] = []
        self.compressions: list[str | None] = []
        self.inventory = [
            SeriesInfo(
                index=0, name="scan A", classification=SeriesClassification.WSI,
                width=10000, height=8000, resolution_count=3, flat_index=0,
            ),
            SeriesInfo(
                index=1, name="scan B", classification=SeriesClassification.WSI,
                width=12000, height=9000, resolution_count=4, flat_index=3,
            ),
            SeriesInfo(
                index=2, name="label", classification=SeriesClassification.ASSOCIATED_IMAGE,
                width=800, height=400,
            ),
            SeriesInfo(
                index=3, name="unknown", classification=SeriesClassification.UNKNOWN,
                width=1000, height=700,
            ),
        ]

    def inspect(
        self, source: Path, *, flatten: bool = False, include_ome_xml: bool = False
    ) -> list[SeriesInfo]:
        if source.name.startswith("."):
            assert self.converted is not None
            return [self.converted]
        return self.inventory

    def convert(
        self,
        source: Path,
        series: int,
        output: Path,
        overwrite: bool = False,
        conversion_series: int | None = None,
        preserve_pyramid: bool = True,
        compression: str | None = None,
    ) -> ConversionResult:
        self.calls.append((series if conversion_series is None else conversion_series, preserve_pyramid))
        self.compressions.append(compression)
        selected = next(item for item in self.inventory if item.index == series)
        self.converted = selected if preserve_pyramid else SeriesInfo(
            index=selected.index, name=selected.name, width=selected.width,
            height=selected.height, resolution_count=1,
        )
        command = ["bfconvert", "-no-upgrade"]
        if preserve_pyramid:
            command.append("-noflat")
        if self.fail_primary and preserve_pyramid:
            return ConversionResult(
                backend="bioformats", source=source, output=output, series=series,
                command=command, started_at="", finished_at="", success=False,
                error="Bio-Formats failed to read a pyramid plane.",
            )
        output.write_bytes(b"test-output")
        return ConversionResult(
            backend="bioformats", source=source, output=output, series=series,
            command=command, started_at="", finished_at="", success=True,
        )


def _pipeline(backend: FakeBackend) -> ConversionPipeline:
    pipeline = ConversionPipeline(
        cast(OpenSlideChecker, Checker()), cast(WSIBackend, backend)
    )
    pipeline.output_checker = Checker()  # type: ignore[assignment]
    return pipeline


def test_only_incompatible_skips_compatible_input(tmp_path: Path) -> None:
    backend = FakeBackend()
    pipeline = _pipeline(backend)

    record = pipeline.convert_all(tmp_path / "already-usable.tif", tmp_path / "out", only_incompatible=True)[0]

    assert record.source_assessment is not None
    assert record.source_assessment.status == AssessmentStatus.COMPATIBLE
    assert record.conversion is None
    assert not backend.calls


def test_convert_all_handles_each_primary_image_and_excludes_ancillary(tmp_path: Path) -> None:
    backend = FakeBackend()
    records = _pipeline(backend).convert_all(tmp_path / "sample.vsi", tmp_path / "out")

    assert [record.selected_name for record in records] == ["scan A", "scan B"]
    assert all(not record.errors for record in records)
    assert all(record.output_bioformats_verification is not None for record in records)
    assert all(record.output_bioformats_verification.passed for record in records if record.output_bioformats_verification)
    assert all(record.output_assessment is not None for record in records)
    assert all("Excluded associated image 'label'" in record.warnings[0] for record in records)
    assert {preserve for _, preserve in backend.calls} == {True}


def test_pipeline_propagates_compression_to_each_image(tmp_path: Path) -> None:
    """Apply the selected compression method consistently across a source batch."""
    backend = FakeBackend()

    records = _pipeline(backend).convert_all(
        tmp_path / "sample.vsi", tmp_path / "out", compression="LZW"
    )

    assert all(not record.errors for record in records)
    assert backend.compressions == ["LZW", "LZW"]


def test_failed_primary_strategy_retries_and_records_both_attempts(tmp_path: Path) -> None:
    backend = FakeBackend(fail_primary=True)

    record = _pipeline(backend).convert(
        tmp_path / "sample.vsi", tmp_path / "out", series_index=0,
        fallback_on_validation_failure=True,
    )

    assert [attempt.strategy for attempt in record.conversion_attempts] == [
        "preserve-pyramid", "flatten-resolutions",
    ]
    assert not record.conversion_attempts[0].passed
    assert record.conversion_attempts[1].passed
    assert not record.errors
    assert record.warnings[-1] == (
        "A conversion attempt failed before a later attempt passed all required checks."
    )
    assert backend.calls == [(0, True), (0, False)]


def test_default_dry_run_plans_all_wsi_images_without_conversion(tmp_path: Path) -> None:
    backend = FakeBackend()

    records = _pipeline(backend).convert_all(
        tmp_path / "sample.vsi", tmp_path / "out", dry_run=True
    )

    assert len(records) == 2
    assert all("Dry run" in record.warnings[-1] for record in records)
    assert not backend.calls


def test_one_preexisting_output_does_not_abort_other_logical_images(tmp_path: Path) -> None:
    backend = FakeBackend()
    output = _output_path(tmp_path / "sample.vsi", tmp_path / "out", backend.inventory[0])
    output.parent.mkdir(parents=True)
    output.write_text("existing", encoding="utf-8")

    records = _pipeline(backend).convert_all(tmp_path / "sample.vsi", tmp_path / "out")

    assert records[0].errors
    assert records[1].selected_name == "scan B"
    assert not records[1].errors


def test_output_path_uses_logical_name_and_source_series_id(tmp_path: Path) -> None:
    source = tmp_path / "sample.vsi"
    first = _output_path(source, tmp_path / "out", SeriesInfo(index=0, name="BF_01 H&E"))
    second = _output_path(source, tmp_path / "out", SeriesInfo(index=1, name="BF_01 H&E"))

    assert first != second
    assert "BF_01 H_E" in first.name
    assert "image-0" in first.name


def test_bioformats_verification_rejects_a_single_level_output() -> None:
    source = SeriesInfo(
        index=2, name="scan", width=10000, height=8000, resolution_count=4
    )
    output = SeriesInfo(
        index=0, name="scan", width=10000, height=8000, resolution_count=1
    )

    verification = _verify_bioformats_output(source, [output])

    assert not verification.passed
    assert verification.matching_series == []
    assert verification.error is not None
    assert "pyramid" in verification.error


def test_flat_verification_accepts_single_series_without_resolution_count() -> None:
    """Bio-Formats omits the count field for valid single-resolution OME-TIFFs."""
    source = SeriesInfo(
        index=2, name="scan", width=25150, height=30472, resolution_count=7
    )
    output = SeriesInfo(index=0, name="scan", width=25150, height=30472)

    verification = _verify_bioformats_output(
        source, [output], preserve_pyramid=False
    )

    assert verification.passed
    assert verification.matching_series == [0]
    assert verification.expected_resolution_count == 1


def test_publish_verified_uses_rename_when_hard_links_are_unsupported(
    tmp_path: Path, monkeypatch
) -> None:
    staging = tmp_path / "staging.ome.tiff"
    destination = tmp_path / "accepted.ome.tiff"
    staging.write_bytes(b"verified image")

    def reject_hardlink(_source, _destination):
        raise OSError(errno.EPERM, "hard links are unsupported")

    monkeypatch.setattr("wsi_converter.pipeline.os.link", reject_hardlink)

    _publish_verified(staging, destination, overwrite=False)

    assert destination.read_bytes() == b"verified image"
    assert not staging.exists()
