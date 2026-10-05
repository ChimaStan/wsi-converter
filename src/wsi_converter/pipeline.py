"""High-level logical-image conversion orchestration."""

from __future__ import annotations

import errno
import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends.base import WSIBackend
from .exceptions import ConversionError, SelectionError, WSIConverterError
from .models import (
    AssessmentStatus,
    BioFormatsVerification,
    ConversionAttempt,
    OpenSlideAssessment,
    OpenSlideRequirements,
    RunRecord,
    SeriesClassification,
    SeriesInfo,
)
from .openslide import OpenSlideChecker
from .series import select_series, select_wsi_series

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, dict[str, Any]], None]


class ConversionPipeline:
    """Coordinate logical-image discovery, conversion, and strict verification."""

    def __init__(
        self,
        checker: OpenSlideChecker | None = None,
        backend: WSIBackend | None = None,
        requirements: OpenSlideRequirements | None = None,
    ) -> None:
        """Configure source assessment and conversion collaborators.

        Args:
            checker: Checker used to assess source files. Converted outputs use
                stricter requirements regardless of this source policy.
            backend: Bio-Formats inspection and conversion backend.
            requirements: Default source-assessment policy when no checker is
                supplied.
        """
        self.checker = checker or OpenSlideChecker(requirements)
        checker_requirements = getattr(checker, "requirements", None)
        require_mpp = bool(
            (requirements and requirements.require_mpp)
            or (checker_requirements and checker_requirements.require_mpp)
        )
        self.output_checker = OpenSlideChecker(
            OpenSlideRequirements(require_pyramid=True, require_mpp=require_mpp)
        )
        self.backend = backend

    def convert(
        self,
        source: Path,
        output_root: Path,
        series_index: int | None = None,
        series_name: str | None = None,
        overwrite: bool = False,
        only_incompatible: bool = False,
        dry_run: bool = False,
        resolution_mode: str = "preserve",
        fallback_on_validation_failure: bool = False,
        keep_partial_artifacts: bool = False,
        progress: ProgressCallback | None = None,
        compression: str | None = None,
    ) -> RunRecord:
        """Convert one explicitly selected or uniquely identified WSI image.

        Multi-image inputs should normally use :meth:`convert_all`, which
        converts every confidently identified primary WSI independently.

        Args:
            source: Input WSI container.
            output_root: Parent directory for per-source outputs.
            series_index: Optional logical Bio-Formats series ID override.
            series_name: Optional exact OME image name override.
            overwrite: Replace a previously verified output if enabled.
            only_incompatible: Skip a source already accepted by OpenSlide.
            dry_run: Inspect and report the plan without converting.
            resolution_mode: ``preserve`` source pyramid levels or explicitly
                ``flatten`` them into separate image series.
            fallback_on_validation_failure: Try the opposite resolution mode
                after a conversion or validation failure.
            keep_partial_artifacts: Retain non-empty output from a failed
                Bio-Formats process for diagnostics.
            progress: Optional callback for live source, image, and attempt events.
            compression: Optional Bio-Formats codec name; omitted means
                uncompressed output.

        Returns:
            Workflow and attempt records for this logical WSI.

        Raises:
            ConversionError: If no backend is configured or output policy fails.
            WSIConverterError: If inspection or safe series selection fails.
        """
        if resolution_mode not in {"preserve", "flatten"}:
            raise ConversionError("Resolution mode must be 'preserve' or 'flatten'.")
        _emit(progress, "source_started", source=Path(source))
        inventory, source_assessment = self._inspect(source, only_incompatible)
        _emit(progress, "source_inspected", source=Path(source), inventory=inventory)
        if source_assessment is not None and only_incompatible and _is_compatible(source_assessment):
            record = self._skipped_record(source, inventory, source_assessment)
            _emit(progress, "image_completed", record=record)
            _emit(progress, "source_completed", source=Path(source), records=[record])
            return record
        selected = select_series(inventory, series_index, series_name)
        _emit(progress, "image_started", source=Path(source), selected=selected,
              resolution_mode=resolution_mode)
        record = self._convert_selected(
            source, output_root, inventory, selected, source_assessment,
            overwrite, dry_run, [], resolution_mode, fallback_on_validation_failure,
            keep_partial_artifacts, progress,
            compression,
        )
        _emit(progress, "image_completed", record=record)
        _emit(progress, "source_completed", source=Path(source), records=[record])
        return record

    def convert_all(
        self,
        source: Path,
        output_root: Path,
        overwrite: bool = False,
        only_incompatible: bool = False,
        dry_run: bool = False,
        resolution_mode: str = "preserve",
        fallback_on_validation_failure: bool = False,
        keep_partial_artifacts: bool = False,
        progress: ProgressCallback | None = None,
        compression: str | None = None,
    ) -> list[RunRecord]:
        """Convert every confidently identified primary WSI in one source.

        Associated images, pyramid levels represented as separate records,
        and unknown records are not converted. Their roles remain available in
        every run's inspection inventory and notes.

        Args:
            source: Input WSI container.
            output_root: Parent directory for per-source outputs.
            overwrite: Replace previously verified outputs if enabled.
            only_incompatible: Skip a source already accepted by OpenSlide.
            dry_run: Inspect and report all planned images without converting.
            resolution_mode: Primary mode, ``preserve`` or ``flatten``.
            fallback_on_validation_failure: Try the other mode only when enabled.
            keep_partial_artifacts: Retain non-empty output from failed processes.
            progress: Optional callback for live source, image, and attempt events.
            compression: Optional Bio-Formats codec name; omitted means
                uncompressed output.

        Returns:
            One workflow record per selected logical WSI. An inspection or
            processing failure is returned as a record so callers can continue
            with other source files.
        """
        if resolution_mode not in {"preserve", "flatten"}:
            raise ConversionError("Resolution mode must be 'preserve' or 'flatten'.")
        _emit(progress, "source_started", source=Path(source))
        try:
            inventory, source_assessment = self._inspect(source, only_incompatible)
        except WSIConverterError as exc:
            record = RunRecord(source=Path(source), started_at=_now(), finished_at=_now(),
                               status="failed", errors=[str(exc)])
            _emit(progress, "source_completed", source=Path(source), record=record)
            return [record]
        _emit(progress, "source_inspected", source=Path(source), inventory=inventory)
        if source_assessment is not None and only_incompatible and _is_compatible(source_assessment):
            record = self._skipped_record(source, inventory, source_assessment)
            _emit(progress, "image_completed", record=record)
            _emit(progress, "source_completed", source=Path(source), record=record)
            return [record]
        notes = _inventory_notes(inventory)
        try:
            candidates = select_wsi_series(inventory)
        except SelectionError as exc:
            record = RunRecord(
                source=Path(source), source_assessment=source_assessment,
                inspection=inventory, started_at=_now(), finished_at=_now(),
                status="skipped", warnings=[*notes, str(exc)],
            )
            _emit(progress, "source_completed", source=Path(source), record=record)
            return [record]
        records: list[RunRecord] = []
        for candidate in candidates:
            _emit(progress, "image_started", source=Path(source), selected=candidate,
                  resolution_mode=resolution_mode)
            try:
                record = self._convert_selected(
                    source, output_root, inventory, candidate, source_assessment,
                    overwrite, dry_run, notes, resolution_mode,
                    fallback_on_validation_failure, keep_partial_artifacts, progress,
                    compression,
                )
            except WSIConverterError as exc:
                record = RunRecord(
                    source=Path(source), source_assessment=source_assessment,
                    inspection=inventory, selected_series=candidate.index,
                    selected_name=candidate.name, started_at=_now(), finished_at=_now(),
                    resolution_mode=resolution_mode,
                    fallback_on_validation_failure=fallback_on_validation_failure,
                    status="failed",
                    warnings=list(notes), errors=[str(exc)],
                )
            except Exception as exc:
                logger.exception("Unexpected per-image failure for %s (%s)", source, candidate.name)
                record = RunRecord(
                    source=Path(source), source_assessment=source_assessment,
                    inspection=inventory, selected_series=candidate.index,
                    selected_name=candidate.name, started_at=_now(), finished_at=_now(),
                    resolution_mode=resolution_mode,
                    fallback_on_validation_failure=fallback_on_validation_failure,
                    status="failed",
                    warnings=list(notes),
                    errors=[f"Unexpected per-image failure: {type(exc).__name__}: {exc}"],
                )
            records.append(record)
            _emit(progress, "image_completed", record=record)
        _emit(progress, "source_completed", source=Path(source), records=records)
        return records

    def _inspect(
        self, source: Path, only_incompatible: bool
    ) -> tuple[list[SeriesInfo], OpenSlideAssessment | None]:
        if self.backend is None:
            raise ConversionError("No conversion backend is configured.")
        source = Path(source)
        assessment = self.checker.check(source) if only_incompatible else None
        if assessment is not None and _is_compatible(assessment):
            return [], assessment
        return self.backend.inspect(source, include_ome_xml=True), assessment

    def _skipped_record(
        self,
        source: Path,
        inventory: list[SeriesInfo],
        assessment: OpenSlideAssessment,
    ) -> RunRecord:
        return RunRecord(
            source=Path(source), source_assessment=assessment, inspection=inventory,
            started_at=_now(), finished_at=_now(),
            status="skipped",
            warnings=["Skipped: source is already usable through OpenSlide."],
        )

    def _convert_selected(
        self,
        source: Path,
        output_root: Path,
        inventory: list[SeriesInfo],
        selected: SeriesInfo,
        source_assessment: OpenSlideAssessment | None,
        overwrite: bool,
        dry_run: bool,
        notes: list[str],
        resolution_mode: str,
        fallback_on_validation_failure: bool,
        keep_partial_artifacts: bool,
        progress: ProgressCallback | None,
        compression: str | None,
    ) -> RunRecord:
        if self.backend is None:
            raise ConversionError("No conversion backend is configured.")
        source, output_root = Path(source), Path(output_root)
        if resolution_mode not in {"preserve", "flatten"}:
            raise ConversionError("Resolution mode must be 'preserve' or 'flatten'.")
        output = _output_path(source, output_root, selected, resolution_mode)
        record = RunRecord(
            source=source, source_assessment=source_assessment, inspection=inventory,
            selected_series=selected.index, selected_name=selected.name,
            resolution_mode=resolution_mode, started_at=_now(), warnings=list(notes),
            fallback_on_validation_failure=fallback_on_validation_failure,
        )
        if output.exists() and not overwrite:
            raise ConversionError(f"Output already exists; pass --overwrite to replace it: {output}")
        if dry_run:
            record.warnings.append(
                f"Dry run: would convert {selected.name!r} (series {selected.index}) "
                f"in {resolution_mode!r} resolution mode to {output}."
            )
            record.status = "planned"
            record.finished_at = _now()
            return record

        attempts: list[ConversionAttempt] = []
        modes = [resolution_mode]
        if fallback_on_validation_failure:
            alternate = "flatten" if resolution_mode == "preserve" else "preserve"
            modes.append(alternate)
        for attempt_mode in modes:
            strategy = "preserve-pyramid" if attempt_mode == "preserve" else "flatten-resolutions"
            preserve_pyramid = attempt_mode == "preserve"
            attempt_output = _output_path(source, output_root, selected, attempt_mode)
            if preserve_pyramid:
                conversion_index = selected.index
            else:
                try:
                    conversion_index = _flat_index(inventory, selected)
                except WSIConverterError as exc:
                    attempts.append(ConversionAttempt(
                        strategy=strategy, mode=attempt_mode, outcome="failed", passed=False,
                        errors=[f"Could not map logical image to flattened Bio-Formats series: {exc}"],
                    ))
                    _emit(progress, "attempt_completed", source=source, selected=selected,
                          attempt=attempts[-1])
                    break
            if attempt_output.exists() and not overwrite and attempt_mode != resolution_mode:
                conflict = ConversionAttempt(
                    strategy=strategy, mode=attempt_mode, outcome="failed",
                    errors=[f"Fallback output already exists; pass --overwrite to replace it: {attempt_output}"],
                )
                attempts.append(conflict)
                _emit(progress, "attempt_completed", source=source, selected=selected,
                      attempt=conflict)
                break
            _emit(progress, "attempt_started", source=source, selected=selected,
                  mode=attempt_mode, strategy=strategy)
            attempt, staging = self._run_attempt(
                source, attempt_output, selected, conversion_index, overwrite,
                preserve_pyramid,
                compression,
            )
            attempts.append(attempt)
            if attempt.passed:
                try:
                    attempt_output.parent.mkdir(parents=True, exist_ok=True)
                    _publish_verified(staging, attempt_output, overwrite)
                except OSError as exc:
                    attempt.passed = False
                    attempt.outcome = "partial"
                    attempt.errors.append(f"Could not publish verified output: {exc}")
                    _retain_artifact(staging, attempt_output, strategy, "unverified", attempt)
                else:
                    attempt.artifact_disposition = "accepted"
                    attempt.artifact_path = attempt_output
                    assert attempt.conversion is not None
                    attempt.conversion.output = attempt_output
                    record.artifact_disposition = "accepted"
                    record.artifact_path = attempt_output
                    record.effective_resolution_mode = attempt_mode
                    if attempt_mode != resolution_mode:
                        record.warnings.append(
                            f"Requested {resolution_mode!r} mode failed; explicitly enabled "
                            f"fallback mode {attempt_mode!r} passed validation."
                        )
                    _emit(progress, "attempt_completed", source=source, selected=selected,
                          attempt=attempt)
                    break
            else:
                if attempt.conversion and attempt.conversion.success:
                    _retain_artifact(staging, attempt_output, strategy, "unverified", attempt)
                elif keep_partial_artifacts and staging.is_file() and staging.stat().st_size:
                    _retain_artifact(staging, attempt_output, strategy, "partial", attempt)
                else:
                    _remove_file(staging)
                    attempt.artifact_disposition = "discarded"
                _emit(progress, "attempt_completed", source=source, selected=selected,
                      attempt=attempt)
                if fallback_on_validation_failure and _is_nonretryable_dependency_error(attempt.errors):
                    break
                continue
            _emit(progress, "attempt_completed", source=source, selected=selected,
                  attempt=attempt)

        record.conversion_attempts = attempts
        if attempts:
            accepted = next((item for item in attempts if item.passed), None)
            last = accepted or attempts[-1]
            record.conversion = last.conversion
            record.output_bioformats_verification = last.bioformats_verification
            record.output_assessment = last.openslide_assessment
            if accepted is None:
                retained = next((item for item in reversed(attempts) if item.artifact_path), None)
                if retained is not None:
                    record.artifact_disposition = retained.artifact_disposition
                    record.artifact_path = retained.artifact_path
                    record.effective_resolution_mode = retained.mode
                completed_conversion = any(
                    item.conversion is not None and item.conversion.success for item in attempts
                )
                record.status = "partial" if completed_conversion else "failed"
            else:
                rejected_attempts = [attempt for attempt in attempts if not attempt.passed]
                record.status = "partial" if rejected_attempts else "passed"
                if rejected_attempts:
                    record.warnings.append(
                        "A conversion attempt failed before a later attempt passed all required checks."
                    )
        if not any(item.passed for item in attempts):
            record.errors.extend(
                f"{attempt.strategy}: {error}"
                for attempt in attempts
                for error in attempt.errors
            )
        record.finished_at = _now()
        return record

    def _run_attempt(
        self,
        source: Path,
        output: Path,
        selected: SeriesInfo,
        conversion_index: int,
        overwrite: bool,
        preserve_pyramid: bool,
        compression: str | None,
    ) -> tuple[ConversionAttempt, Path]:
        """Convert to an isolated staging file, then run both validators."""
        assert self.backend is not None
        strategy = "preserve-pyramid" if preserve_pyramid else "flatten-resolutions"
        staging = _staging_path(output, strategy)
        staging.parent.mkdir(parents=True, exist_ok=True)
        attempt = ConversionAttempt(
            strategy=strategy, mode="preserve" if preserve_pyramid else "flatten"
        )
        try:
            attempt.conversion = self.backend.convert(
                source, selected.index, staging, overwrite,
                conversion_series=conversion_index,
                preserve_pyramid=preserve_pyramid,
                compression=compression,
            )
            if not attempt.conversion.success:
                attempt.errors.append(attempt.conversion.error or "Bio-Formats conversion failed.")
                attempt.outcome = "failed"
                return attempt, staging
            try:
                output_inventory = self.backend.inspect(staging, include_ome_xml=False)
            except WSIConverterError as exc:
                verification = BioFormatsVerification(
                    passed=False,
                    expected_dimensions=_dimensions(selected),
                    expected_resolution_count=(selected.resolution_count if preserve_pyramid else 1),
                    error=f"Bio-Formats could not reopen the converted output: {exc}",
                )
            else:
                verification = _verify_bioformats_output(
                    selected, output_inventory, preserve_pyramid=preserve_pyramid
                )
            attempt.bioformats_verification = verification
            if not verification.passed:
                attempt.errors.append(verification.error or "Bio-Formats output validation failed.")
            checker = self.output_checker
            if not preserve_pyramid:
                requirements = OpenSlideRequirements(
                    require_pyramid=False,
                    require_mpp=getattr(
                        getattr(self.output_checker, "requirements", None), "require_mpp", False
                    ),
                )
                attempt.openslide_assessment = checker.check(staging, requirements)
            else:
                attempt.openslide_assessment = checker.check(staging)
            if attempt.openslide_assessment.status not in {
                AssessmentStatus.COMPATIBLE, AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
            }:
                attempt.errors.extend(
                    attempt.openslide_assessment.errors
                    or [f"OpenSlide validation status: {attempt.openslide_assessment.status.value}"]
                )
            attempt.passed = (
                bool(attempt.conversion and attempt.conversion.success)
                and bool(attempt.bioformats_verification and attempt.bioformats_verification.passed)
                and attempt.openslide_assessment.status in {
                    AssessmentStatus.COMPATIBLE, AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
                }
            )
            attempt.outcome = "passed" if attempt.passed else "partial"
            attempt.artifact_disposition = "accepted" if attempt.passed else "unverified"
        except Exception as exc:
            logger.exception("Unexpected %s attempt failure for %s", strategy, source)
            attempt.errors.append(f"Unexpected failure during {strategy}: {type(exc).__name__}: {exc}")
            attempt.outcome = "partial" if attempt.conversion and attempt.conversion.success else "failed"
        return attempt, staging


def _verify_bioformats_output(
    selected: SeriesInfo,
    output_inventory: list[SeriesInfo],
    *,
    preserve_pyramid: bool = True,
) -> BioFormatsVerification:
    """Check dimensions and the resolution structure required by one mode."""
    expected = _dimensions(selected)
    expected_count = selected.resolution_count if preserve_pyramid else 1
    observed_dimensions = [
        dims for item in output_inventory if (dims := _dimensions(item)) is not None
    ]
    observed_counts = [item.resolution_count for item in output_inventory]
    matching = [
        item for item in output_inventory
        if expected is not None and _dimensions(item) == expected
    ]
    if preserve_pyramid:
        valid = [
            item for item in matching
            if item.resolution_count is not None
            and item.resolution_count > 1
            and (expected_count is None or item.resolution_count == expected_count)
        ]
        passed = bool(valid) if expected is not None else any(
            item.resolution_count is not None and item.resolution_count > 1
            for item in output_inventory
        )
    else:
        # Bio-Formats omits the ``Resolutions`` field for a single-resolution
        # OME-TIFF. In flatten mode, one matching output series with no count is
        # therefore the expected one-level representation.
        valid = [
            item for item in output_inventory
            if (expected is None or _dimensions(item) == expected)
            and (
                item.resolution_count == 1
                or (item.resolution_count is None and len(output_inventory) == 1)
            )
        ]
        passed = bool(valid)
    if passed:
        error = None
    elif not matching:
        error = "Bio-Formats output dimensions did not match the selected logical image."
    else:
        counts = [item.resolution_count for item in matching]
        if preserve_pyramid:
            error = (
                f"Bio-Formats output pyramid did not match the source: expected {expected_count} "
                f"resolution(s), observed {counts}; at least two resolutions are required."
            )
        else:
            error = f"Flattened mode requires one output series with one resolution; observed {counts}."
    return BioFormatsVerification(
        passed=passed,
        expected_dimensions=expected,
        observed_dimensions=observed_dimensions,
        matching_series=[item.index for item in valid],
        expected_resolution_count=expected_count,
        observed_resolution_counts=observed_counts,
        error=error,
    )


def _flat_index(inventory: list[SeriesInfo], selected: SeriesInfo) -> int:
    """Find the flattened reader index from preceding resolution counts."""
    if selected.flat_index is not None:
        return selected.flat_index
    expected = _dimensions(selected)
    if expected is None:
        raise ConversionError(f"Logical image {selected.name!r} has no dimensions for flat-index mapping.")
    try:
        position = inventory.index(selected)
    except ValueError as exc:
        raise ConversionError(f"Logical image {selected.name!r} is not in the inventory.") from exc
    offset = 0
    for previous in inventory[:position]:
        if previous.resolution_count is None:
            raise ConversionError(
                f"Resolution count is missing for preceding image {previous.name!r}; flat index is unknown."
            )
        offset += previous.resolution_count
    return offset


def _inventory_notes(inventory: list[SeriesInfo]) -> list[str]:
    """Describe intentionally excluded ancillary and unresolved records."""
    notes: list[str] = []
    for item in inventory:
        if item.classification == SeriesClassification.ASSOCIATED_IMAGE:
            notes.append(f"Excluded associated image {item.name!r} (series {item.index}).")
        elif item.classification == SeriesClassification.UNKNOWN:
            notes.append(
                f"Not converted: {item.name!r} (series {item.index}) is unclassified; "
                f"{item.classification_reason or 'insufficient metadata'}."
            )
    return notes


def _is_compatible(assessment: OpenSlideAssessment) -> bool:
    return assessment.status in {
        AssessmentStatus.COMPATIBLE, AssessmentStatus.COMPATIBLE_WITH_WARNINGS,
    }


def _is_nonretryable_dependency_error(errors: list[str]) -> bool:
    text = " ".join(errors).casefold()
    return any(token in text for token in ("executable not found", "could not run bio-formats", "command timed out"))


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove failed staging output %s", path, exc_info=True)


def _publish_verified(staging: Path, destination: Path, overwrite: bool) -> None:
    """Publish without replacing a destination unless overwrite was requested."""
    if overwrite:
        os.replace(staging, destination)
        return
    try:
        os.link(staging, destination)
    except OSError as exc:
        # Some mounted filesystems (including common WSL drive mounts) do not
        # support hard links. Fall back to a same-filesystem rename after an
        # existence check; the staging and destination paths share a parent.
        unsupported_link_errors = {
            errno.EPERM,
            errno.EXDEV,
            getattr(errno, "ENOTSUP", errno.EPERM),
            getattr(errno, "EOPNOTSUPP", errno.EPERM),
        }
        if exc.errno not in unsupported_link_errors:
            raise
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staging, destination)
        return
    try:
        staging.unlink()
    except OSError:
        logger.warning("Published output but could not remove duplicate staging link %s", staging)


def _retain_artifact(
    staging: Path,
    requested_output: Path,
    strategy: str,
    disposition: str,
    attempt: ConversionAttempt,
) -> None:
    """Move a rejected artifact to a clearly separated diagnostic directory."""
    if not staging.is_file() or staging.stat().st_size == 0:
        attempt.artifact_disposition = "not-created"
        return
    token = uuid.uuid4().hex[:8]
    suffix = "partial" if disposition == "partial" else "unverified"
    name = requested_output.name.removesuffix(".ome.tiff")
    source_root = requested_output.parent.parent
    diagnostic_dir = source_root / "unverified"
    destination = diagnostic_dir / f"{name}.{strategy}.{suffix}.{token}.ome.tiff"
    try:
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
    except OSError as exc:
        attempt.errors.append(f"Could not retain unverified artifact at {destination}: {exc}")
        attempt.artifact_disposition = "unverified-retention-failed"
        attempt.artifact_path = staging if staging.is_file() and staging.stat().st_size else None
        if attempt.conversion is not None and attempt.artifact_path is not None:
            attempt.conversion.output = attempt.artifact_path
        return
    attempt.artifact_disposition = disposition
    attempt.artifact_path = destination
    if attempt.conversion is not None:
        attempt.conversion.output = destination


def _output_path(
    source: Path, root: Path, series: SeriesInfo, resolution_mode: str = "preserve"
) -> Path:
    """Build a stable per-source output under a mode-specific directory."""
    clean_name = "".join(char if char.isalnum() or char in "-_. " else "_" for char in series.name)
    clean_name = clean_name.strip(" .") or f"image-{series.index}"
    category = "wsi" if resolution_mode == "preserve" else "flattened"
    return root / source.stem / category / f"{clean_name} [image-{series.index}].ome.tiff"


def _staging_path(output: Path, strategy: str) -> Path:
    token = uuid.uuid4().hex
    return output.with_name(f".{output.stem}.{strategy}.{token}.ome.tiff")


def _dimensions(series: SeriesInfo) -> tuple[int, int] | None:
    if series.width is None or series.height is None:
        return None
    return series.width, series.height


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(
    callback: ProgressCallback | None, event: str, **payload: Any
) -> None:
    if callback is not None:
        callback(event, payload)
