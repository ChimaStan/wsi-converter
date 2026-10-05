"""OpenSlide assessment shared by pre-conversion checks and verification."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .models import AssessmentStatus, CheckResult, OpenSlideAssessment, OpenSlideRequirements

logger = logging.getLogger(__name__)


class OpenSlideChecker:
    """Open real files, inspect metadata, and read representative small regions."""

    def __init__(self, requirements: OpenSlideRequirements | None = None) -> None:
        """Create a checker using the supplied policy or project defaults.

        Args:
            requirements: Default set of checks for calls that do not pass a
                per-call override. If omitted, ``OpenSlideRequirements``
                defaults are used.
        """
        self.requirements = requirements or OpenSlideRequirements()

    def check(
        self, path: Path, requirements: OpenSlideRequirements | None = None
    ) -> OpenSlideAssessment:
        """Assess one actual image file through OpenSlide.

        The result captures metadata, requirement outcomes, and small read
        probes. Open failures and missing native bindings are represented as
        ``UNREADABLE`` rather than raised as low-level library exceptions.
        An optional per-call policy overrides the checker's configured policy.

        Args:
            path: Image file to open and assess. The file is never modified.
            requirements: Optional policy for this call; defaults to the
                checker's configured requirements.

        Returns:
            Structured metadata, individual check results, warnings, errors,
            and an overall compatibility status.
        """
        path = Path(path)
        req = requirements or self.requirements
        result = OpenSlideAssessment(path=path, status=AssessmentStatus.UNREADABLE)
        if not path.exists() or not path.is_file():
            result.errors.append("Input file does not exist or is not a regular file.")
            result.tests.append(CheckResult("file_access", "FAIL", True, result.errors[-1]))
            return result
        result.tests.append(CheckResult("file_access", "PASS", True, "File is accessible."))
        if path.suffix.lower() == ".vsi":
            companion = path.with_name(f"_{path.stem}_")
            result.vsi_companion = "FOUND" if companion.is_dir() else "NOT FOUND"
            if result.vsi_companion == "NOT FOUND":
                result.warnings.append(f"Expected VSI companion directory was not found: {companion}")

        try:
            import openslide
        except ImportError as exc:
            result.errors.append("OpenSlide Python bindings are unavailable; install the 'openslide' extra.")
            result.tests.append(CheckResult("openslide_open", "FAIL", True, str(exc)))
            return result

        slide: Any = None
        try:
            slide = openslide.OpenSlide(str(path))
            props = slide.properties
            result.detected_format = props.get("openslide.vendor")
            result.openslide_version = getattr(openslide, "__library_version__", None)
            result.dimensions = tuple(slide.dimensions)
            result.level_count = int(slide.level_count)
            result.level_dimensions = [tuple(dim) for dim in slide.level_dimensions]
            result.downsamples = [float(value) for value in slide.level_downsamples]
            result.mpp_x = _float_or_none(props.get("openslide.mpp-x"))
            result.mpp_y = _float_or_none(props.get("openslide.mpp-y"))
            result.associated_images = sorted(slide.associated_images)
            result.tile_width = _int_or_none(props.get("openslide.level[0].tile-width"))
            result.tile_height = _int_or_none(props.get("openslide.level[0].tile-height"))
            result.tests.append(CheckResult("openslide_open", "PASS", req.require_open, "OpenSlide opened the file."))
            result.tests.append(CheckResult("metadata", "PASS", req.require_dimensions, "Dimensions and pyramid metadata read."))
            self._requirement_test(result, "dimensions", result.dimensions[0] > 0 and result.dimensions[1] > 0,
                                   req.require_dimensions, "Valid base-level dimensions are available.")
            self._requirement_test(result, "pyramid", result.level_count > 1,
                                   req.require_pyramid, f"{result.level_count} level(s) detected.")
            self._requirement_test(result, "mpp", result.mpp_x is not None and result.mpp_y is not None,
                                   req.require_mpp, "Physical pixel size metadata.")
            self._requirement_test(result, "associated_images", bool(result.associated_images),
                                   req.require_associated_images,
                                   f"{len(result.associated_images)} associated image(s) detected.")
            if req.require_tile_reads:
                try:
                    reads = _read_representative_regions(slide, req)
                    result.tests.append(CheckResult("tile_reads", "PASS", True,
                                                    f"Read {len(reads)} representative region(s): {', '.join(reads)}."))
                except (openslide.OpenSlideError, OSError, ValueError) as exc:
                    result.tests.append(CheckResult("tile_reads", "FAIL", True, str(exc)))
                    result.errors.append(f"Representative tile read failed: {exc}")
            else:
                result.tests.append(CheckResult("tile_reads", "SKIP", False, "Not required."))
            required_failures = [test for test in result.tests if test.required and test.status == "FAIL"]
            optional_failures = [test for test in result.tests if not test.required and test.status == "FAIL"]
            if required_failures:
                result.status = AssessmentStatus.INCOMPATIBLE
            elif optional_failures or result.warnings:
                result.status = AssessmentStatus.COMPATIBLE_WITH_WARNINGS
            else:
                result.status = AssessmentStatus.COMPATIBLE
        except (openslide.OpenSlideError, OSError, ValueError) as exc:
            logger.debug("OpenSlide failed to read %s", path, exc_info=True)
            result.errors.append(f"OpenSlide could not open or inspect the input file: {exc}")
            result.tests.append(CheckResult("openslide_open", "FAIL", req.require_open, str(exc)))
            result.status = AssessmentStatus.UNREADABLE
        finally:
            if slide is not None:
                slide.close()
        return result

    @staticmethod
    def _requirement_test(
        result: OpenSlideAssessment, name: str, passed: bool, required: bool, details: str
    ) -> None:
        """Record a capability check and classify a required miss as an error.

        Args:
            result: Assessment receiving the check and any diagnostic.
            name: Stable name for the capability being checked.
            passed: Whether the capability was observed.
            required: Whether failure makes the assessment incompatible.
            details: Human-readable evidence recorded with the result.
        """
        status = "PASS" if passed else ("FAIL" if required else "SKIP")
        result.tests.append(CheckResult(name, status, required, details))
        if not passed:
            message = f"Required capability unavailable: {name}."
            if required:
                result.errors.append(message)
            elif name == "mpp":
                result.warnings.append(message)


def _read_representative_regions(slide: Any, req: OpenSlideRequirements) -> list[str]:
    """Read small base-level corner/centre regions and one intermediate level.

    Args:
        slide: OpenSlide-like object exposing dimensions, levels, and
            ``read_region``.
        req: Read-size and intermediate-level policy.

    Returns:
        Labels for each successfully read location. Read errors propagate to
        the caller so they can be recorded as a failed check.
    """
    width, height = slide.dimensions
    size = max(1, req.tile_size)
    coords = {
        "upper-left": (0, 0),
        "centre": (max(0, width // 2 - size // 2), max(0, height // 2 - size // 2)),
        "lower-right": (max(0, width - size), max(0, height - size)),
    }
    completed: list[str] = []
    for label, (x, y) in coords.items():
        region = slide.read_region((x, y), 0, (min(size, width), min(size, height)))
        region.getpixel((0, 0))
        completed.append(label)
    if req.check_intermediate_level and slide.level_count > 1:
        level = max(1, slide.level_count // 2)
        level_width, level_height = slide.level_dimensions[level]
        region = slide.read_region((0, 0), level, (min(size, level_width), min(size, level_height)))
        region.getpixel((0, 0))
        completed.append(f"level-{level}")
    return completed


def _float_or_none(value: Any) -> float | None:
    """Parse optional numeric metadata as float, returning ``None`` if invalid.

    Args:
        value: Raw property value from OpenSlide.

    Returns:
        Parsed float, or ``None`` for missing or malformed values.
    """
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    """Parse optional numeric metadata as integer, returning ``None`` if invalid.

    Args:
        value: Raw property value from OpenSlide.

    Returns:
        Parsed integer, or ``None`` for missing or malformed values.
    """
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
