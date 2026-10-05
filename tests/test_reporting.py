"""Unit tests for structured report serialization."""

import json
from pathlib import Path

from wsi_converter.models import AssessmentStatus, OpenSlideAssessment
from wsi_converter.reporting import write_json


def test_json_report_serializes_paths_and_enums(tmp_path: Path) -> None:
    """Serialize model paths and status enums as ordinary JSON values."""
    target = tmp_path / "report.json"
    item = OpenSlideAssessment(path=tmp_path / "slide.vsi", status=AssessmentStatus.UNREADABLE)

    write_json({"result": item}, target)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["result"]["path"].endswith("slide.vsi")
    assert data["result"]["status"] == "UNREADABLE"
