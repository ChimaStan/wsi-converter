"""Opt-in Bio-Formats and OpenSlide integration checks for a local WSI."""

import os
from pathlib import Path

import pytest

from wsi_converter.backends.bioformats import BioFormatsBackend
from wsi_converter.models import AssessmentStatus, SeriesClassification
from wsi_converter.pipeline import ConversionPipeline
from wsi_converter.series import select_wsi_series

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("WSI_CONVERTER_TEST_VSI"), reason="Set WSI_CONVERTER_TEST_VSI to a local VSI")
def test_local_vsi_logical_inventory_and_optional_conversion(tmp_path: Path) -> None:
    """Inspect logical image names and, when requested, strictly verify one conversion."""
    source = Path(os.environ["WSI_CONVERTER_TEST_VSI"])
    backend = BioFormatsBackend()
    pipeline = ConversionPipeline(backend=backend)
    series = os.environ.get("WSI_CONVERTER_TEST_SERIES")
    if series is None:
        plan = pipeline.convert_all(source, tmp_path, dry_run=True)
        assert plan and all(not record.errors for record in plan)
        assert all("Dry run" in record.warnings[-1] for record in plan)
        inventory = plan[0].inspection
        candidates = select_wsi_series(inventory)
        assert [record.selected_series for record in plan] == [item.index for item in candidates]
        assert all(item.resolution_count and item.resolution_count > 1 for item in candidates)
        assert all(item.classification == SeriesClassification.WSI for item in candidates)
        return
    record = pipeline.convert(source, tmp_path, series_index=int(series))
    candidates = select_wsi_series(record.inspection)
    assert all(item.resolution_count and item.resolution_count > 1 for item in candidates)

    assert record.conversion_attempts
    assert any(attempt.passed for attempt in record.conversion_attempts), (
        "No strategy passed both validators: "
        + repr([(attempt.strategy, attempt.errors) for attempt in record.conversion_attempts])
    )
    assert not record.errors
    assert record.output_bioformats_verification is not None
    assert record.output_bioformats_verification.passed
    assert record.output_bioformats_verification.expected_resolution_count is not None
    assert record.output_assessment is not None
    assert record.output_assessment.status in {
        AssessmentStatus.COMPATIBLE,
        AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
    }
    assert record.output_assessment.level_count is not None
    assert record.output_assessment.level_count > 1
