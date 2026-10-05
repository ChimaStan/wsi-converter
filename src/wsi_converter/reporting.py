"""JSON and spreadsheet-friendly CSV reporting."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import OpenSlideAssessment, RunRecord, to_primitive


def write_json(value: Any, destination: Path | None = None) -> str:
    """Serialize supported model values and optionally write a UTF-8 JSON file.

    The formatted payload is returned in both file and stdout-oriented use.

    Args:
        value: Supported model or nested structure to serialize.
        destination: Optional file path. Parent directories are created when
            needed; omit it to return the payload without writing a file.

    Returns:
        Indented JSON text without a trailing newline.

    Raises:
        OSError: If the destination cannot be created or written.
        TypeError: If ``value`` contains an unsupported non-JSON value.
    """
    payload = json.dumps(to_primitive(value), indent=2, ensure_ascii=False)
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
    return payload


def write_check_csv(results: list[OpenSlideAssessment], destination: Path) -> None:
    """Write one row per assessment as a UTF-8 CSV with spreadsheet BOM.

    Args:
        results: Per-file check outcomes to tabulate.
        destination: Output CSV path; its parent directory is created if needed.

    Raises:
        OSError: If the destination cannot be created or written.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(check_csv_text(results), encoding="utf-8-sig", newline="")


def check_csv_text(results: list[OpenSlideAssessment]) -> str:
    """Return the compact tabular assessment report as CSV text.

    Args:
        results: Per-file check outcomes to tabulate.

    Returns:
        CSV text with a header row and one row per assessment.
    """
    buffer = io.StringIO(newline="")
    fields = [
        "file", "extension", "detected_format", "openslide_status", "dimensions", "level_count",
        "mpp_x", "mpp_y", "tile_width", "tile_height", "read_test_status", "vsi_companion",
        "warnings", "errors", "final_status",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for item in results:
        reads = next((test.status for test in item.tests if test.name == "tile_reads"), "SKIP")
        writer.writerow({
                "file": str(item.path), "extension": item.path.suffix.lower(),
                "detected_format": item.detected_format or "", "openslide_status": item.status.value,
                "dimensions": "x".join(map(str, item.dimensions)) if item.dimensions else "",
                "level_count": item.level_count if item.level_count is not None else "",
                "mpp_x": item.mpp_x if item.mpp_x is not None else "",
                "mpp_y": item.mpp_y if item.mpp_y is not None else "",
                "tile_width": item.tile_width if item.tile_width is not None else "",
                "tile_height": item.tile_height if item.tile_height is not None else "",
                "read_test_status": reads, "vsi_companion": item.vsi_companion or "",
                "warnings": " | ".join(item.warnings), "errors": " | ".join(item.errors),
                "final_status": item.status.value,
        })
    return buffer.getvalue()


def write_run_record(record: RunRecord, destination: Path) -> None:
    """Write one source-to-verification provenance record as JSON.

    Args:
        record: Workflow record to serialize.
        destination: JSON output path; parent directories are created as needed.

    Raises:
        OSError: If the destination cannot be created or written.
    """
    write_json(record, destination)
