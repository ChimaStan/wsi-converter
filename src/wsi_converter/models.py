"""Small, serializable data models shared by workflow stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AssessmentStatus(str, Enum):
    """Outcome of an OpenSlide assessment against configured requirements.

    ``COMPATIBLE_WITH_WARNINGS`` passes every required check but carries
    non-fatal diagnostics. ``INCOMPATIBLE`` has a required check failure after
    opening. ``UNREADABLE`` could not be opened or inspected.
    """

    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_WARNINGS = "COMPATIBLE_WITH_WARNINGS"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNREADABLE = "UNREADABLE"


class SeriesClassification(str, Enum):
    """Best-supported role of a Bio-Formats series in the source slide.

    ``UNKNOWN`` is retained when metadata does not justify a confident WSI or
    associated-image classification.
    """

    WSI = "WSI"
    ASSOCIATED_IMAGE = "ASSOCIATED_IMAGE"
    PYRAMID_LEVEL = "PYRAMID_LEVEL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OpenSlideRequirements:
    """Checks that an OpenSlide assessment must satisfy.

    Optional metadata checks can be enabled for workflows that depend on those
    capabilities. An image must still open before metadata or pixels can be
    inspected; ``require_open`` marks opening as an explicit required check.
    ``tile_size`` limits each representative pixel read.

    Attributes:
        require_open: Whether a successful OpenSlide open is mandatory.
        require_dimensions: Whether positive base-level dimensions are required.
        require_pyramid: Whether more than one pyramid level is required.
        require_tile_reads: Whether representative pixel reads must succeed.
        require_mpp: Whether both physical pixel-size values are mandatory.
        require_associated_images: Whether at least one associated image is required.
        tile_size: Maximum width and height for each sampled region, in pixels.
        check_intermediate_level: Whether to sample one non-base pyramid level.
    """

    require_open: bool = True
    require_dimensions: bool = True
    require_pyramid: bool = False
    require_tile_reads: bool = True
    require_mpp: bool = False
    require_associated_images: bool = False
    tile_size: int = 64
    check_intermediate_level: bool = True


@dataclass
class CheckResult:
    """Result and explanation for one named check in an assessment.

    ``status`` is ``PASS``, ``FAIL``, or ``SKIP``; ``required`` determines
    whether a failed check affects the assessment's compatibility status.

    Attributes:
        name: Stable identifier for the check.
        status: Outcome string: ``PASS``, ``FAIL``, or ``SKIP``.
        required: Whether a failure affects the overall assessment status.
        details: Evidence or explanation for this outcome.
    """

    name: str
    status: str
    required: bool
    details: str = ""


@dataclass
class OpenSlideAssessment:
    """Metadata, per-check outcomes, and final status for one WSI path.

    ``INCOMPATIBLE`` means OpenSlide opened the image but a required capability
    failed; ``UNREADABLE`` means the image could not be opened or inspected.

    Attributes:
        path: Source or converted file that was checked.
        status: Overall result derived from required checks.
        detected_format: OpenSlide vendor identifier, when present.
        openslide_version: Native OpenSlide library version, when available.
        dimensions: Base-level width and height in pixels.
        level_count: Number of pyramid levels exposed by OpenSlide.
        level_dimensions: Width/height pairs for all levels.
        downsamples: Downsample factors relative to level zero.
        tile_width: Vendor tile width metadata, when exposed.
        tile_height: Vendor tile height metadata, when exposed.
        mpp_x: Physical pixel width in microns, when available.
        mpp_y: Physical pixel height in microns, when available.
        associated_images: Names exposed by OpenSlide for associated images.
        tests: Individual check outcomes.
        warnings: Non-fatal diagnostics.
        errors: Required failures or read/open errors.
        vsi_companion: Expected VSI companion-data state when applicable.
    """

    path: Path
    status: AssessmentStatus
    detected_format: str | None = None
    openslide_version: str | None = None
    dimensions: tuple[int, int] | None = None
    level_count: int | None = None
    level_dimensions: list[tuple[int, int]] = field(default_factory=list)
    downsamples: list[float] = field(default_factory=list)
    tile_width: int | None = None
    tile_height: int | None = None
    mpp_x: float | None = None
    mpp_y: float | None = None
    associated_images: list[str] = field(default_factory=list)
    tests: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    vsi_companion: str | None = None


@dataclass
class SeriesInfo:
    """Normalized metadata used to classify and select one Bio-Formats series.

    Attributes:
        index: Source-specific Bio-Formats series index.
        name: Series name from Bio-Formats, or a generated fallback.
        classification: Conservative WSI/reference/unknown role.
        width: X dimension in pixels, when reported.
        height: Y dimension in pixels, when reported.
        axes: Available dimension-order and axis-size summary.
        pixel_type: Bio-Formats pixel type, when reported.
        channels: Channel count, when reported.
        mpp_x: Physical pixel width, when reported.
        mpp_y: Physical pixel height, when reported.
        resolution_count: Number of resolutions, when reported.
        flat_index: Series index when resolutions are flattened, if inspected.
        classification_reason: Metadata evidence behind the assigned role.
        raw_metadata: Backend-specific metadata retained for diagnostics.
    """

    index: int
    name: str
    classification: SeriesClassification = SeriesClassification.UNKNOWN
    width: int | None = None
    height: int | None = None
    axes: str | None = None
    pixel_type: str | None = None
    channels: int | None = None
    mpp_x: float | None = None
    mpp_y: float | None = None
    resolution_count: int | None = None
    flat_index: int | None = None
    classification_reason: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversionResult:
    """Bio-Formats execution details for one selected series and output path.

    ``success`` describes conversion only; callers must use a separate
    OpenSlide assessment before claiming the output is verified.

    Attributes:
        backend: Backend identifier, currently ``bioformats``.
        source: Unmodified input file path.
        output: Requested output file path.
        series: Source-specific series index used by the command.
        command: Argument vector executed by the backend.
        started_at: UTC start time in ISO 8601 format.
        finished_at: UTC finish time in ISO 8601 format.
        success: Whether Bio-Formats created a non-empty output successfully.
        backend_version: Bio-Formats version banner, when available.
        warnings: Non-fatal conversion diagnostics.
        error: Failure explanation, if conversion did not succeed.
    """

    backend: str
    source: Path
    output: Path
    series: int
    command: list[str]
    started_at: str
    finished_at: str
    success: bool
    backend_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BioFormatsVerification:
    """Metadata-level validation of a converted output through Bio-Formats.

    Preservation mode requires the source pyramid structure. Flatten mode
    requires exactly one output resolution. Both modes require matching base
    dimensions when source dimensions are available.

    Attributes:
        passed: Whether Bio-Formats reopened the output and found the expected
            full-resolution dimensions.
        expected_dimensions: Selected input series dimensions, when available.
        observed_dimensions: Dimensions Bio-Formats reports for output series.
        matching_series: Output series indices matching expected dimensions.
        error: Explanation when the output could not be inspected or matched.
    """

    passed: bool
    expected_dimensions: tuple[int, int] | None = None
    observed_dimensions: list[tuple[int, int]] = field(default_factory=list)
    matching_series: list[int] = field(default_factory=list)
    expected_resolution_count: int | None = None
    observed_resolution_counts: list[int | None] = field(default_factory=list)
    error: str | None = None


@dataclass
class ConversionAttempt:
    """One requested resolution mode and its independent verification results.

    Attributes:
        strategy: Human-readable Bio-Formats conversion strategy.
        mode: Resolution handling requested for this attempt.
        conversion: Bio-Formats process result, including its exact arguments.
        bioformats_verification: Independent metadata and pyramid check.
        openslide_assessment: Independent readability and compatibility check.
        outcome: ``passed``, ``partial``, or ``failed`` for this attempt.
        passed: Whether this attempt is accepted for its requested mode.
        artifact_disposition: Accepted, unverified, partial, discarded, or not created.
        artifact_path: Final retained path, when an artifact exists.
        errors: Failures that explain a partial or failed outcome.
    """

    strategy: str
    mode: str = "preserve"
    conversion: ConversionResult | None = None
    bioformats_verification: BioFormatsVerification | None = None
    openslide_assessment: OpenSlideAssessment | None = None
    outcome: str = "failed"
    passed: bool = False
    artifact_disposition: str = "discarded"
    artifact_path: Path | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class RunRecord:
    """Provenance chain linking source assessment, conversion, and verification.

    Fields may remain unset when a run is skipped, fails early, or is a dry run.

    Attributes:
        source: Original input path.
        source_assessment: Pre-conversion OpenSlide outcome, if completed.
        inspection: Bio-Formats series inventory, if inspection was performed.
        selected_series: Chosen source-specific series index, if any.
        selected_name: Logical OME image name, if selected.
        resolution_mode: Requested resolution handling.
        effective_resolution_mode: Mode that produced the retained artifact.
        fallback_on_validation_failure: Whether an alternate mode was enabled.
        status: Image outcome: passed, partial, failed, planned, or skipped.
        artifact_disposition: Accepted, unverified, partial, or discarded.
        artifact_path: Destination or diagnostic artifact path, when retained.
        conversion: Bio-Formats execution result, if conversion was attempted.
        output_bioformats_verification: BioFormatsVerification, if output was
            independently reopened by Bio-Formats.
        output_assessment: Post-conversion OpenSlide result, if available.
        started_at: UTC pipeline start time.
        finished_at: UTC pipeline finish time, empty while still running.
        warnings: Non-fatal workflow decisions and diagnostics.
        errors: Conversion or verification failures.
    """

    source: Path
    source_assessment: OpenSlideAssessment | None = None
    inspection: list[SeriesInfo] = field(default_factory=list)
    selected_series: int | None = None
    selected_name: str | None = None
    resolution_mode: str | None = None
    effective_resolution_mode: str | None = None
    fallback_on_validation_failure: bool = False
    status: str = "pending"
    artifact_disposition: str | None = None
    artifact_path: Path | None = None
    conversion: ConversionResult | None = None
    conversion_attempts: list[ConversionAttempt] = field(default_factory=list)
    output_bioformats_verification: BioFormatsVerification | None = None
    output_assessment: OpenSlideAssessment | None = None
    started_at: str = ""
    finished_at: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def to_primitive(value: Any) -> Any:
    """Convert nested dataclasses, enums, and paths to JSON-compatible values.

    Mappings and sequences are converted recursively; primitive values pass
    through unchanged.

    Args:
        value: Object or nested structure to normalize.

    Returns:
        A value composed of JSON-compatible primitives, dictionaries, and lists.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
