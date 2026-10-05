"""Command-line interface for assessment, inspection, conversion, and verification."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends.bioformats import BioFormatsBackend, BioFormatsRunner
from .discovery import discover
from .environment import diagnose_environment, diagnostics_to_dict
from .exceptions import WSIConverterError
from .models import (
    AssessmentStatus,
    BioFormatsVerification,
    ConversionAttempt,
    OpenSlideAssessment,
    OpenSlideRequirements,
    RunRecord,
    SeriesClassification,
    SeriesInfo,
    to_primitive,
)
from .openslide import OpenSlideChecker
from .pipeline import ConversionPipeline
from .reporting import check_csv_text, write_check_csv, write_json

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all supported workflow commands.

    Returns:
        Parser with ``check``, ``inspect``, ``convert``, ``verify``, and
        ``doctor`` subcommands and their command-specific options.
    """
    parser = argparse.ArgumentParser(prog="wsi-converter", description="Assess and convert whole-slide images.")
    parser.add_argument("--verbose", action="store_true", help="Show debug-level diagnostic logs.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Assess one or more files with OpenSlide.")
    check.add_argument("path", type=Path, nargs="+", help="Input files or directories to assess.")
    check.add_argument("--recursive", action="store_true", help="Search input directories recursively.")
    check.add_argument("--output", type=Path,
                       help="Directory in which to write compatibility.json and compatibility.csv.")
    check.add_argument("--require-pyramid", action="store_true",
                       help="Fail compatibility when OpenSlide exposes fewer than two levels.")
    check.add_argument("--require-mpp", action="store_true",
                       help="Fail compatibility when physical pixel size is unavailable.")
    check.add_argument("--json", nargs="?", const="-", metavar="PATH",
                       help="Write detailed JSON to PATH; without PATH, print JSON to stdout.")
    check.add_argument("--csv", nargs="?", const="-", metavar="PATH",
                       help="Write summary CSV to PATH; without PATH, print CSV to stdout.")

    inspect = sub.add_parser("inspect", help="Inspect Bio-Formats series metadata.")
    inspect.add_argument("path", type=Path, help="Image file to inspect with Bio-Formats.")
    _add_bioformats_options(inspect)
    inspect.add_argument("--json", nargs="?", const="-", metavar="PATH",
                         help="Write inspection metadata to PATH; without PATH, print JSON.")

    convert = sub.add_parser(
        "convert", help="Convert logical WSI images and report independent validation results."
    )
    convert.add_argument("path", type=Path, nargs="+", help="Input WSI files or directories.")
    convert.add_argument("--output", type=Path, default=Path("converted"),
                         help="Output root directory (default: ./converted).")
    convert.add_argument("--recursive", action="store_true",
                         help="Process supported files found in input directories recursively.")
    selection = convert.add_mutually_exclusive_group()
    selection.add_argument("--series", type=int,
                           help="Convert only this logical Bio-Formats series ID (advanced override).")
    selection.add_argument("--series-name", help="Convert only the exact OME image name from inspect.")
    convert.add_argument("--resolution-mode", choices=("preserve", "flatten"), default="preserve",
                         help="Preserve the source pyramid with -noflat (default), or export one flattened resolution.")
    convert.add_argument("--compression", metavar="METHOD", type=_compression_method,
                         help="Bio-Formats compression method (for example LZW); omitted means uncompressed. "
                              "Available methods depend on Bio-Formats and the OME-TIFF writer.")
    convert.add_argument("--fallback-on-validation-failure", action="store_true",
                         help="After conversion or validation failure, try the other mode; disabled by default.")
    convert.add_argument("--keep-partial-artifacts", action="store_true",
                         help="Retain partial output from a failed Bio-Formats process; may use substantial storage.")
    convert.add_argument("--only-incompatible", action="store_true",
                         help="Skip a source that already passes the configured OpenSlide checks.")
    convert.add_argument("--dry-run", action="store_true",
                         help="Inspect and list planned outputs without converting images.")
    convert.add_argument("--overwrite", action="store_true",
                         help="Replace an existing accepted output at the selected mode's destination.")
    convert.add_argument("--require-mpp", action="store_true",
                         help="Require physical pixel-size metadata during OpenSlide validation.")
    convert.add_argument("--json", nargs="?", const="-", metavar="PATH",
                         help="Write a live, atomically updated report to PATH; without PATH, print final JSON.")
    _add_bioformats_options(convert)

    verify = sub.add_parser("verify", help="Run the shared OpenSlide checks on a file.")
    verify.add_argument("path", type=Path, nargs="+", help="Converted image files or directories to verify.")
    verify.add_argument("--recursive", action="store_true", help="Search input directories recursively.")
    verify.add_argument("--require-pyramid", action="store_true",
                        help="Require at least two OpenSlide pyramid levels.")
    verify.add_argument("--require-mpp", action="store_true",
                        help="Require physical pixel-size metadata.")
    verify.add_argument("--json", nargs="?", const="-", metavar="PATH",
                        help="Write validation results to PATH; without PATH, print JSON.")

    doctor = sub.add_parser("doctor", help="Diagnose Python and external runtime dependencies.")
    _add_bioformats_options(doctor)
    doctor.add_argument("--json", nargs="?", const="-", metavar="PATH",
                        help="Write dependency diagnostics to PATH; without PATH, print JSON.")
    return parser


def _add_bioformats_options(parser: argparse.ArgumentParser) -> None:
    """Add external Bio-Formats executable and installation-path options.

    Args:
        parser: Subcommand parser that should accept executable paths or an
            installation root.
    """
    parser.add_argument("--bfconvert", type=Path, help="Explicit path to the bfconvert executable.")
    parser.add_argument("--showinf", type=Path, help="Explicit path to the showinf executable.")
    parser.add_argument("--bioformats-home", type=Path,
                        help="Bio-Formats command directory (also settable with BIOFORMATS_HOME).")


def _compression_method(value: str) -> str:
    """Normalize and validate a Bio-Formats compression method argument."""
    method = value.strip()
    if not method:
        raise argparse.ArgumentTypeError("compression method cannot be empty")
    return method


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its process exit status.

    Assessment findings are reported as results; expected operational errors
    are logged and returned as a non-zero status.

    Args:
        argv: Optional argument sequence. When omitted, arguments are read from
            the process command line.

    Returns:
        Exit status: zero for successful execution, non-zero for command errors
        or failed dependency diagnostics.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.command in {"check", "verify"}:
            return _run_check(args)
        if args.command == "inspect":
            return _run_inspect(args)
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "doctor":
            return _run_doctor(args)
    except WSIConverterError as exc:
        logger.error("%s", exc)
        return 2
    return 0


def _checker(args: argparse.Namespace) -> OpenSlideChecker:
    """Create the shared checker using requirements selected on the CLI.

    Args:
        args: Parsed namespace with ``require_pyramid`` and ``require_mpp``.

    Returns:
        Checker configured to apply those requirements consistently.
    """
    return OpenSlideChecker(OpenSlideRequirements(
        require_pyramid=getattr(args, "require_pyramid", False),
        require_mpp=args.require_mpp,
    ))


def _run_check(args: argparse.Namespace) -> int:
    """Discover inputs, assess each with OpenSlide, and write requested reports.

    Args:
        args: Parsed ``check`` or ``verify`` command options.

    Returns:
        Zero when assessment completed, even if some files were incompatible.
    """
    found = discover(args.path, recursive=args.recursive)
    checker = _checker(args)
    results = [checker.check(path) for path in found.files]
    if found.inaccessible:
        for issue in found.inaccessible:
            logger.warning("Discovery: %s", issue)
    if getattr(args, "output", None):
        args.output.mkdir(parents=True, exist_ok=True)
        write_json({"results": results, "inaccessible": found.inaccessible}, args.output / "compatibility.json")
        write_check_csv(results, args.output / "compatibility.csv")
    if args.json:
        payload = write_json({"results": results, "inaccessible": found.inaccessible},
                             None if args.json == "-" else Path(args.json))
        if args.json == "-":
            print(payload)
    if getattr(args, "csv", None):
        if args.csv == "-":
            print(check_csv_text(results), end="")
        else:
            write_check_csv(results, Path(args.csv))
    if not args.json and not getattr(args, "csv", None):
        _print_summary(results)
    return 0  # An incompatible file is a finding, not a failed assessment command.


def _run_inspect(args: argparse.Namespace) -> int:
    """Inspect one source with Bio-Formats and print series metadata.

    Args:
        args: Parsed ``inspect`` options, including the source and tool paths.

    Returns:
        Zero after successful inspection and reporting.
    """
    backend = _backend(args)
    series = backend.inspect(args.path, include_ome_xml=True)
    payload = [{**to_primitive(item)} for item in series]
    if args.json:
        output = write_json(payload, None if args.json == "-" else Path(args.json))
        if args.json == "-":
            print(output)
    else:
        for item in series:
            print(f"Series {item.index}: {item.name} [{item.classification.value}] "
                  f"{item.width or '?'} x {item.height or '?'} "
                  f"({item.resolution_count or '?'} resolution(s))")
            if item.classification_reason:
                print(f"  reason: {item.classification_reason}")
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    """Print dependency diagnostics and return failure if any check fails.

    Args:
        args: Parsed ``doctor`` options for Bio-Formats paths and JSON output.

    Returns:
        Zero only when every required dependency check passes.
    """
    diagnostics = diagnose_environment(args.bioformats_home, args.bfconvert, args.showinf)
    if args.json != "-":
        for item in diagnostics:
            location = f" | {item.location}" if item.location else ""
            version = f" | {item.version}" if item.version else ""
            detail = f" | {item.detail}" if item.detail else ""
            print(f"{item.name:22} {item.status}{location}{version}{detail}")
    if args.json:
        payload = write_json(diagnostics_to_dict(diagnostics),
                             None if args.json == "-" else Path(args.json))
        if args.json == "-":
            print(payload)
    return 0 if all(item.passed for item in diagnostics) else 1


def _run_convert(args: argparse.Namespace) -> int:
    """Run conversion for discovered inputs and optionally serialize provenance.

    Args:
        args: Parsed ``convert`` options, including selection, output, and
            overwrite/dry-run policy.

    Returns:
        Zero when all planned runs finish without conversion or verification
        errors; otherwise a non-zero command status.
    """
    found = discover(args.path, recursive=args.recursive)
    for issue in found.inaccessible:
        logger.warning("Discovery: %s", issue)
    pipeline = ConversionPipeline(_checker(args), _backend(args))
    report = _new_conversion_report(args, found.inaccessible, len(found.files))
    report_path = Path(args.json) if args.json and args.json != "-" else None
    if report_path:
        write_json(report, report_path)

    def progress(event: str, payload: dict[str, Any]) -> None:
        _update_conversion_report(report, event, payload, report_path, args.dry_run)

    for source in found.files:
        try:
            if args.series is None and args.series_name is None:
                pipeline.convert_all(
                    source, args.output, args.overwrite, args.only_incompatible,
                    args.dry_run, args.resolution_mode,
                    args.fallback_on_validation_failure, args.keep_partial_artifacts,
                    progress, compression=args.compression,
                )
            else:
                pipeline.convert(
                    source, args.output, args.series, args.series_name,
                    args.overwrite, args.only_incompatible, args.dry_run,
                    args.resolution_mode, args.fallback_on_validation_failure,
                    args.keep_partial_artifacts, progress, compression=args.compression,
                )
        except WSIConverterError as exc:
            logger.error("%s: %s", source, exc)
            progress("source_failed", {"source": source, "errors": [str(exc)]})
        except Exception as exc:
            logger.exception("Unexpected conversion failure for %s", source)
            progress("source_failed", {
                "source": source,
                "errors": [f"Unexpected failure: {type(exc).__name__}: {exc}"],
            })

    report["state"] = "completed"
    report["finished_at"] = _now()
    report["current"] = None
    _refresh_conversion_report(report, dry_run=args.dry_run, complete=True)
    if report_path:
        write_json(report, report_path)
    if args.json == "-":
        print(write_json(report))
    failed = bool(
        report["summary"]["failed"] or report["summary"]["partial"]
        or report["summary"]["failed_sources"]
    )
    failed = failed or bool(found.inaccessible)
    return 2 if failed else 0


def _new_conversion_report(
    args: argparse.Namespace, inaccessible: list[str], discovered_sources: int
) -> dict[str, Any]:
    """Create the first live snapshot for a conversion run."""
    started = _now()
    return {
        "schema_version": 3,
        "state": "running",
        "outcome": None,
        "provisional_outcome": "running",
        "started_at": started,
        "updated_at": started,
        "finished_at": None,
        "conversion_options": {
            "resolution_mode": args.resolution_mode,
            "compression": args.compression or "uncompressed",
            "fallback_on_validation_failure": args.fallback_on_validation_failure,
            "keep_partial_artifacts": args.keep_partial_artifacts,
            "recursive": args.recursive,
            "dry_run": args.dry_run,
        },
        "summary": {
            "sources_processed": 0,
            "sources_discovered": discovered_sources,
            "eligible_wsi_images": 0,
            "images_in_progress": 0,
            "passed": 0,
            "partial": 0,
            "failed": 0,
            "planned": 0,
            "skipped": 0,
            "failed_sources": 0,
            "passed_sources": 0,
            "partial_sources": 0,
            "skipped_sources": 0,
            "inaccessible_sources": len(inaccessible),
        },
        "sources": [],
        "inaccessible": to_primitive(inaccessible),
        "current": None,
    }


def _update_conversion_report(
    report: dict[str, Any],
    event: str,
    payload: dict[str, Any],
    destination: Path | None,
    dry_run: bool,
) -> None:
    """Apply one pipeline progress event and persist a live JSON snapshot."""
    source = str(payload.get("source", ""))
    sources = report["sources"]
    assert isinstance(sources, list)
    source_report = next((item for item in sources if item["path"] == source), None)
    if event == "source_started":
        if source_report is None:
            source_report = {
                "path": source, "state": "running", "outcome": "running",
                "inspection": None, "notes": [], "images": [], "errors": [],
            }
            sources.append(source_report)
        report["current"] = {"source": source, "state": "inspecting"}
        print(f"INSPECTING {source}", flush=True)
    elif source_report is None:
        source_report = {
            "path": source, "state": "running", "outcome": "running",
            "inspection": None, "notes": [], "images": [], "errors": [],
        }
        sources.append(source_report)

    if event == "source_inspected" and source_report is not None:
        inventory = payload.get("inventory", [])
        source_report["inspection"] = {
            "status": "passed",
            "series_count": len(inventory) if isinstance(inventory, list) else 0,
            "primary_wsi_count": sum(
                item.classification == SeriesClassification.WSI for item in inventory
            ) if isinstance(inventory, list) else 0,
            "series": [
                _series_report_record(item) for item in inventory
            ] if isinstance(inventory, list) else [],
        }
    elif event == "image_started" and source_report is not None:
        selected = payload["selected"]
        image = {
            "series_id": selected.index,
            "name": selected.name,
            "state": "running",
            "outcome": "running",
            "resolution_mode": payload["resolution_mode"],
            "attempts": [],
            "started_at": _now(),
        }
        source_report["images"].append(image)
        report["current"] = {
            "source": source, "series_id": selected.index,
            "name": selected.name, "resolution_mode": payload["resolution_mode"],
            "state": "converting",
        }
        print(f"PROCESSING {source} [{selected.name}, series {selected.index}] "
              f"mode={payload['resolution_mode']}", flush=True)
    elif event == "attempt_started" and source_report is not None:
        selected = payload["selected"]
        image_entry = _find_report_image(source_report, selected.index)
        if image_entry is not None:
            image_entry["current_attempt"] = {
                "mode": payload["mode"], "strategy": payload["strategy"], "state": "running",
            }
        report["current"] = {
            "source": source, "series_id": selected.index, "name": selected.name,
            "mode": payload["mode"], "strategy": payload["strategy"], "state": "converting",
        }
        print(f"  ATTEMPT mode={payload['mode']} strategy={payload['strategy']}", flush=True)
    elif event == "attempt_completed" and source_report is not None:
        selected = payload["selected"]
        image_entry = _find_report_image(source_report, selected.index)
        if image_entry is not None:
            image_entry.setdefault("attempts", []).append(_attempt_report_record(payload["attempt"]))
            image_entry.pop("current_attempt", None)
    elif event == "image_completed" and source_report is not None:
        record = payload["record"]
        if record.selected_series is None:
            source_report["outcome"] = record.status
            source_report["notes"].extend(record.warnings)
        else:
            image_record = _image_report_record(record)
            image_entry = _find_report_image(source_report, record.selected_series)
            if image_entry is None:
                source_report["images"].append(image_record)
            else:
                image_entry.clear()
                image_entry.update(image_record)
        report["current"] = None
        _print_run(record, dry_run)
    elif event == "source_completed" and source_report is not None:
        records = payload.get("records")
        if isinstance(records, list):
            image_statuses = [record.status for record in records]
        else:
            record = payload.get("record")
            image_statuses = [record.status] if record is not None else []
            if record is not None and record.errors:
                if record.selected_series is None:
                    source_report["errors"].extend(_concise_diagnostics(record.errors))
                if record.selected_series is not None:
                    _upsert_report_image(source_report, _image_report_record(record))
            if record is not None and record.selected_series is None:
                source_report["notes"].extend(record.warnings)
        source_report["state"] = "completed"
        source_report["outcome"] = _aggregate_outcome(
            image_statuses, bool(source_report["errors"]), dry_run
        )
        current = report.get("current")
        if isinstance(current, dict) and current.get("source") == source:
            report["current"] = None
    elif event == "source_failed" and source_report is not None:
        errors = _concise_diagnostics(payload.get("errors", []))
        current = report.get("current")
        if isinstance(current, dict) and current.get("source") == source:
            series_id = current.get("series_id")
            image_entry = _find_report_image(source_report, series_id)
            if image_entry is not None:
                image_entry["state"] = "completed"
                image_entry["outcome"] = "failed"
                image_entry["artifact"] = {"disposition": "not-created", "path": None}
                image_entry["attempts"] = []
                image_entry["diagnostics"] = errors
                image_entry["finished_at"] = _now()
            else:
                source_report["errors"].extend(errors)
        else:
            source_report["errors"].extend(errors)
        source_report["state"] = "completed"
        report["current"] = None

    _refresh_conversion_report(report, dry_run=dry_run, complete=False)
    report["updated_at"] = _now()
    if destination is not None:
        try:
            write_json(report, destination)
        except OSError:
            logger.exception("Could not update live conversion report %s", destination)


def _image_report_record(record: RunRecord) -> dict[str, Any]:
    """Serialize a concise outcome for one logical WSI image."""
    attempt_diagnostics = {
        message
        for attempt in record.conversion_attempts
        for message in _concise_diagnostics(attempt.errors)
    }
    diagnostics = [
        message for message in _concise_diagnostics(record.errors)
        if message not in attempt_diagnostics
    ]
    result: dict[str, Any] = {
        "series_id": record.selected_series,
        "name": record.selected_name,
        "state": "completed",
        "outcome": record.status,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "attempts": [_attempt_report_record(attempt) for attempt in record.conversion_attempts],
    }
    if record.effective_resolution_mode is not None:
        result["effective_resolution_mode"] = record.effective_resolution_mode
    if record.artifact_disposition is not None or record.artifact_path is not None:
        result["artifact"] = {
            "disposition": record.artifact_disposition,
            "path": to_primitive(record.artifact_path),
        }
    if diagnostics:
        result["diagnostics"] = diagnostics
    plan = next(
        (warning.removeprefix("Dry run: ") for warning in record.warnings
         if warning.startswith("Dry run:")),
        None,
    )
    if plan:
        result["plan"] = plan
    return result


def _series_report_record(series: SeriesInfo) -> dict[str, Any]:
    """Return just the source-series facts needed to understand selection."""
    result: dict[str, Any] = {
        "series_id": series.index,
        "name": series.name,
        "classification": series.classification.value,
    }
    if series.width is not None and series.height is not None:
        result["dimensions"] = [series.width, series.height]
    if series.resolution_count is not None:
        result["resolution_count"] = series.resolution_count
    if series.mpp_x is not None or series.mpp_y is not None:
        result["mpp"] = [series.mpp_x, series.mpp_y]
    if series.classification_reason:
        result["classification_reason"] = series.classification_reason
    return result


def _attempt_report_record(attempt: ConversionAttempt) -> dict[str, Any]:
    """Serialize conversion and independent checks without raw process output."""
    conversion = attempt.conversion
    bioformats = attempt.bioformats_verification
    openslide = attempt.openslide_assessment
    conversion_report: dict[str, Any] = {
        "status": "not_run" if conversion is None else "passed" if conversion.success else "failed",
    }
    if conversion is not None:
        conversion_report["series_id"] = conversion.series
        if conversion.backend_version:
            conversion_report["backend_version"] = conversion.backend_version
        if conversion.error:
            conversion_report["message"] = _concise_diagnostic(conversion.error)
    result: dict[str, Any] = {
        "mode": attempt.mode,
        "outcome": attempt.outcome,
        "conversion": conversion_report,
        "checks": {
            "bioformats": _bioformats_check_report(bioformats),
            "openslide": _openslide_check_report(openslide),
        },
        "artifact": {
            "disposition": attempt.artifact_disposition,
            "path": to_primitive(attempt.artifact_path),
        },
    }
    all_messages = _concise_diagnostics(attempt.errors)
    categorized = {
        result["conversion"].get("message"),
        result["checks"]["bioformats"].get("message"),
        *result["checks"]["openslide"].get("errors", []),
    }
    extra_messages = [message for message in all_messages if message not in categorized]
    if extra_messages:
        result["diagnostics"] = extra_messages
    return result


def _bioformats_check_report(verification: BioFormatsVerification | None) -> dict[str, Any]:
    """Summarize Bio-Formats metadata validation for one conversion attempt."""
    if verification is None:
        return {"status": "not_run"}
    result: dict[str, Any] = {
        "status": "passed" if verification.passed else "failed",
    }
    if verification.expected_dimensions is not None:
        result["expected_dimensions"] = to_primitive(verification.expected_dimensions)
    if verification.observed_dimensions:
        result["observed_dimensions"] = to_primitive(verification.observed_dimensions)
    if verification.expected_resolution_count is not None:
        result["expected_resolution_count"] = verification.expected_resolution_count
    if verification.observed_resolution_counts:
        result["observed_resolution_counts"] = verification.observed_resolution_counts
    if verification.error:
        result["message"] = _concise_diagnostic(verification.error)
    return result


def _openslide_check_report(assessment: OpenSlideAssessment | None) -> dict[str, Any]:
    """Summarize OpenSlide compatibility and only its failed checks."""
    if assessment is None:
        return {"status": "not_run"}
    failed_checks = [
        {"name": check.name, "message": _concise_diagnostic(check.details) or "Check failed."}
        for check in assessment.tests
        if check.status == "FAIL"
    ]
    result: dict[str, Any] = {
        "status": assessment.status.value.lower(),
    }
    if assessment.detected_format:
        result["format"] = assessment.detected_format
    if assessment.dimensions is not None:
        result["dimensions"] = to_primitive(assessment.dimensions)
    if assessment.level_count is not None:
        result["level_count"] = assessment.level_count
    if assessment.mpp_x is not None or assessment.mpp_y is not None:
        result["mpp"] = [assessment.mpp_x, assessment.mpp_y]
    if failed_checks:
        result["failed_checks"] = failed_checks
    elif assessment.errors:
        result["errors"] = _concise_diagnostics(assessment.errors)
    return result


def _concise_diagnostics(messages: list[str]) -> list[str]:
    """Normalize, deduplicate, and bound report diagnostics."""
    result: list[str] = []
    for message in messages:
        concise = _concise_diagnostic(message)
        if concise and concise not in result:
            result.append(concise)
    return result


def _concise_diagnostic(message: str | None) -> str | None:
    """Extract a useful root-cause line from verbose tool output."""
    if not message:
        return None
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    root_cause = next(
        (line for line in reversed(lines) if "Exception in thread" in line), None
    )
    if root_cause is None:
        candidates = [
            line for line in lines
            if not line.startswith("at ") and not line.startswith("CellSensReader initializing")
        ]
        root_cause = candidates[-1] if candidates else lines[0]
    if len(root_cause) > 500:
        root_cause = root_cause[:497] + "..."
    return root_cause


def _find_report_image(source_report: dict[str, Any], series_id: int | None) -> dict[str, Any] | None:
    images = source_report["images"]
    if not isinstance(images, list):
        return None
    return next((item for item in images if item.get("series_id") == series_id), None)


def _upsert_report_image(source_report: dict[str, Any], image: dict[str, Any]) -> None:
    current = _find_report_image(source_report, image["series_id"])
    if current is None:
        source_report["images"].append(image)
    else:
        current.clear()
        current.update(image)


def _refresh_conversion_report(
    report: dict[str, Any], *, dry_run: bool, complete: bool
) -> None:
    """Recompute hierarchical outcomes and counts from completed image records."""
    sources = report["sources"]
    image_statuses: list[str] = []
    in_progress = 0
    for source in sources:
        images = source["images"]
        statuses = [str(image.get("outcome", "running")) for image in images]
        image_statuses.extend(statuses)
        in_progress += sum(image.get("state") == "running" for image in images)
        source_errors = bool(source.get("errors"))
        source["summary"] = {
            "eligible_wsi_images": len(images),
            "passed": statuses.count("passed"),
            "partial": statuses.count("partial"),
            "failed": statuses.count("failed"),
            "planned": statuses.count("planned"),
            "skipped": statuses.count("skipped"),
            "images_in_progress": sum(image.get("state") == "running" for image in images),
        }
        if source.get("state") == "completed":
            source["outcome"] = _aggregate_outcome(statuses, source_errors, dry_run)
    summary = {
        "sources_processed": len(sources),
        "sources_discovered": report["summary"]["sources_discovered"],
        "eligible_wsi_images": len(image_statuses),
        "images_in_progress": in_progress,
        "passed": image_statuses.count("passed"),
        "partial": image_statuses.count("partial"),
        "failed": image_statuses.count("failed"),
        "planned": image_statuses.count("planned"),
        "skipped": image_statuses.count("skipped"),
        "failed_sources": sum(source.get("outcome") == "failed" for source in sources),
        "passed_sources": sum(source.get("outcome") == "passed" for source in sources),
        "partial_sources": sum(source.get("outcome") == "partial" for source in sources),
        "skipped_sources": sum(source.get("outcome") == "skipped" for source in sources),
        "inaccessible_sources": len(report["inaccessible"]),
    }
    report["summary"] = summary
    errors_exist = bool(
        summary["failed"] or summary["partial"] or summary["failed_sources"]
        or summary["inaccessible_sources"]
    )
    if complete:
        report["outcome"] = _aggregate_outcome(
            image_statuses,
            errors_exist or any(source.get("errors") for source in sources),
            dry_run,
        )
        report.pop("provisional_outcome", None)
    else:
        report["provisional_outcome"] = "partial" if errors_exist else "running"


def _aggregate_outcome(statuses: list[str], has_errors: bool, dry_run: bool) -> str:
    """Roll up child outcomes into one stable run or source outcome."""
    if statuses and all(status == "skipped" for status in statuses) and not has_errors:
        return "skipped"
    if statuses and dry_run and not has_errors and all(
        status in {"planned", "skipped"} for status in statuses
    ):
        return "planned"
    completed_work = any(status in {"passed", "partial"} for status in statuses)
    if completed_work:
        if has_errors or any(status in {"partial", "failed"} for status in statuses):
            return "partial"
        return "passed"
    if has_errors or "failed" in statuses:
        return "failed"
    if statuses and all(status == "skipped" for status in statuses):
        return "skipped"
    if "planned" in statuses:
        return "planned"
    return "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backend(args: argparse.Namespace) -> BioFormatsBackend:
    """Create the Bio-Formats backend with the requested external tool paths.

    Args:
        args: Parsed namespace containing optional executable paths and home.

    Returns:
        Backend configured for lazy external executable resolution.
    """
    return BioFormatsBackend(BioFormatsRunner(
        args.bfconvert, args.showinf, bioformats_home=args.bioformats_home
    ))


def _print_summary(results: list[OpenSlideAssessment]) -> None:
    """Print per-file assessment results and a status count summary.

    Args:
        results: Assessment records to display.
    """
    counts = Counter(item.status.value for item in results)
    print(f"Files scanned:             {len(results)}")
    print(f"Compatible:                {counts[AssessmentStatus.COMPATIBLE.value]}")
    print(f"Compatible with warnings:   {counts[AssessmentStatus.COMPATIBLE_WITH_WARNINGS.value]}")
    print(f"Incompatible:               {counts[AssessmentStatus.INCOMPATIBLE.value]}")
    print(f"Unreadable:                  {counts[AssessmentStatus.UNREADABLE.value]}")
    for item in results:
        print(f"{item.status.value:26} {item.path}")
        for warning in item.warnings:
            print(f"  warning: {warning}")
        for error in item.errors:
            print(f"  error: {error}")


def _print_run(record: RunRecord, dry_run: bool) -> None:
    """Print a concise outcome for one conversion pipeline record.

    Args:
        record: Pipeline result for one source image.
        dry_run: Whether a planned conversion should be labelled as a dry run.
    """
    plan = next((warning for warning in record.warnings if warning.startswith("Dry run:")), None)
    if dry_run and plan:
        print(f"PLANNED {record.source} [{record.selected_name or 'source'}]: {plan}")
    elif record.status == "passed":
        label = "VALIDATED" if record.effective_resolution_mode == "flatten" else "VERIFIED"
        print(f"{label} {record.source} [{record.selected_name or 'source'}] "
              f"mode={record.effective_resolution_mode} output={record.artifact_path}")
    elif record.status == "partial":
        print(f"PARTIAL {record.source} [{record.selected_name or 'source'}] "
              f"mode={record.resolution_mode} artifact={record.artifact_path or 'not retained'}")
    elif record.status == "failed" or record.errors:
        print(f"FAILED {record.source} [{record.selected_name or 'source'}]: {'; '.join(record.errors)}")
    elif record.warnings:
        print(f"SKIPPED {record.source}: {'; '.join(record.warnings)}")
    else:
        print(f"NO OUTPUT {record.source} [{record.selected_name or 'source'}]")
    for attempt in record.conversion_attempts:
        conversion = "PASS" if attempt.conversion and attempt.conversion.success else "FAIL"
        bioformats = (
            "NOT_RUN" if attempt.bioformats_verification is None
            else "PASS" if attempt.bioformats_verification.passed else "FAIL"
        )
        openslide = (
            "NOT_RUN" if attempt.openslide_assessment is None
            else "PASS" if attempt.openslide_assessment.status in {
                AssessmentStatus.COMPATIBLE, AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
            } else "FAIL"
        )
        print(f"  attempt mode={attempt.mode} conversion={conversion} "
              f"bioformats={bioformats} openslide={openslide} "
              f"artifact={attempt.artifact_disposition}: {attempt.artifact_path or '-'}")
        for error in attempt.errors:
            print(f"    error: {error}")
    for warning in record.warnings:
        if not (dry_run and warning.startswith("Dry run:")):
            print(f"  note: {warning}")


if __name__ == "__main__":
    sys.exit(main())
