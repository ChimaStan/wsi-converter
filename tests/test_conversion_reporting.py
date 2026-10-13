"""Synthetic tests for compact conversion report outcomes and CSV summaries."""

from argparse import Namespace
import csv
from datetime import datetime, timezone
import io
from pathlib import Path

from wsi_converter.cli import (
    _finalize_conversion_report,
    _image_report_record,
    _now,
    _new_conversion_report,
    _update_conversion_report,
)
from wsi_converter.backends.bioformats.backend import _now as backend_now
from wsi_converter.models import ConversionAttempt, RunRecord
from wsi_converter.pipeline import _now as pipeline_now
from wsi_converter.reporting import conversion_csv_text


def _report(*, dry_run: bool = False) -> dict:
    args = Namespace(
        resolution_mode="preserve", compression=None,
        fallback_on_validation_failure=True, keep_partial_artifacts=False,
        recursive=True, dry_run=dry_run,
    )
    return _new_conversion_report(args, [], 1)


def test_runtime_timestamps_use_utc_z_suffix() -> None:
    for timestamp in (_now(), pipeline_now(), backend_now()):
        assert timestamp.endswith("Z")
        assert not timestamp.endswith("+00:00")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed.tzinfo == timezone.utc



def test_completed_record_is_attached_to_source_and_success_summarized() -> None:
    report = _report()
    record = RunRecord(
        source=Path("/inputs/case-a.slide"), selected_series=2,
        selected_name="Tissue", resolution_mode="preserve",
        effective_resolution_mode="preserve", status="passed",
        artifact_disposition="accepted", artifact_path=Path("/outputs/tissue.ome.tiff"),
        started_at="2026-10-13T10:00:00+00:00", finished_at="2026-10-13T10:05:00+00:00",
    )

    # The pipeline's image_completed event intentionally carries only the record.
    _update_conversion_report(report, "image_completed", {"record": record}, None, False)
    _update_conversion_report(
        report, "source_completed", {"source": record.source, "records": [record]}, None, False
    )
    report["state"] = "completed"
    _finalize_conversion_report(report, dry_run=False)

    assert report["sources"][0]["path"] == str(record.source)
    assert report["sources"][0]["images"][0]["outcome"] == "passed"
    assert report["summary"]["input_files"]["passed"] == 1
    assert report["summary"]["logical_wsi_images"]["passed"] == 1
    assert "in_progress" not in report["summary"]["logical_wsi_images"]
    assert report["outcome"] == "passed"


def test_failed_source_is_counted_and_has_no_orphan_running_image() -> None:
    report = _report()
    _update_conversion_report(
        report, "source_failed",
        {"source": "/inputs/bad.slide", "errors": ["Could not inspect file."]}, None, False,
    )
    report["discovered_sources"] = 1
    report["state"] = "completed"
    _finalize_conversion_report(report, dry_run=False)

    assert report["outcome"] == "failed"
    assert report["summary"]["input_files"]["failed"] == 1
    assert report["sources"][0]["errors"] == ["Could not inspect file."]
    assert not report["sources"][0]["images"]


def test_csv_has_one_row_per_logical_image_and_uses_requested_mode() -> None:
    report = {
        "conversion_options": {"requested_resolution_mode": "preserve"},
        "sources": [{
            "path": "/inputs/case-a.slide", "outcome": "passed", "images": [{
                "series_id": 2, "name": "Tissue", "outcome": "passed",
                "effective_resolution_mode": "flatten", "fallback_used": True,
                "artifact": {"disposition": "accepted", "path": "/outputs/tissue.ome.tiff"},
                "attempts": [{"mode": "flatten", "outcome": "passed", "checks": {
                    "bioformats": {"status": "passed"},
                    "openslide": {"status": "compatible", "dimensions": [100, 200],
                                   "level_count": 1},
                }}],
            }],
        }],
    }

    rows = list(csv.DictReader(io.StringIO(conversion_csv_text(report))))

    assert len(rows) == 1
    assert rows[0]["requested_resolution_mode"] == "preserve"
    assert rows[0]["effective_resolution_mode"] == "flatten"
    assert rows[0]["fallback_used"] == "True"
    assert rows[0]["image_outcome"] == "passed"


def test_successful_fallback_is_passed_and_records_both_attempts() -> None:
    record = RunRecord(
        source=Path("/inputs/pyramid.slide"), selected_series=0, selected_name="Tissue",
        resolution_mode="preserve", effective_resolution_mode="flatten", status="passed",
        conversion_attempts=[
            ConversionAttempt(
                strategy="preserve-pyramid", mode="preserve", outcome="partial",
                passed=False, artifact_disposition="discarded",
                errors=["Preserved output failed validation."],
            ),
            ConversionAttempt(
                strategy="flatten-resolutions", mode="flatten", outcome="passed",
                passed=True, artifact_disposition="accepted",
                artifact_path=Path("/output/tissue.ome.tiff"),
            ),
        ],
    )

    serialized = _image_report_record(record)

    assert serialized["outcome"] == "passed"
    assert serialized["effective_resolution_mode"] == "flatten"
    assert serialized["fallback_used"] is True
    assert [item["mode"] for item in serialized["attempts"]] == ["preserve", "flatten"]


def test_mixed_success_and_partial_image_results_make_partial_batch() -> None:
    report = _report()
    source_path = Path("/inputs/mixed.slide")
    records = [
        RunRecord(source=source_path, selected_series=0, selected_name="A", status="passed"),
        RunRecord(source=source_path, selected_series=1, selected_name="B", status="partial"),
    ]

    _update_conversion_report(report, "source_completed", {
        "source": source_path, "records": records,
    }, None, False)
    _finalize_conversion_report(report, dry_run=False)

    assert report["outcome"] == "partial"
    assert report["summary"]["logical_wsi_images"]["passed"] == 1
    assert report["summary"]["logical_wsi_images"]["partial"] == 1
