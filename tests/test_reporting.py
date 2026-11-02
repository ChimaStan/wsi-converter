"""Unit tests for structured report serialization."""

import json
from pathlib import Path

from wsi_converter.models import AssessmentStatus, OpenSlideAssessment
from wsi_converter.reporting import check_csv_text, write_json


def test_json_report_serializes_paths_and_enums(tmp_path: Path) -> None:
    """Serialize model paths and status enums as ordinary JSON values."""
    target = tmp_path / "report.json"
    item = OpenSlideAssessment(path=tmp_path / "slide.vsi", status=AssessmentStatus.UNREADABLE)

    write_json({"result": item}, target)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["result"]["path"].endswith("slide.vsi")
    assert data["result"]["status"] == "UNREADABLE"


def test_check_csv_includes_objective_metadata() -> None:
    result = OpenSlideAssessment(
        path=Path("slide.ome.tiff"),
        status=AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
        mpp_x=0.25,
        mpp_y=None,
        objective_power=20.0,
        aperio_app_mag=None,
    )

    text = check_csv_text([result])

    assert "objective_power" in text.splitlines()[0]
    assert "aperio_app_mag" in text.splitlines()[0]
    assert "20.0" in text.splitlines()[1]
