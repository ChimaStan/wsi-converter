"""Conservative Bio-Formats series classification and selection."""

from __future__ import annotations

from .exceptions import SelectionError
from .models import SeriesClassification, SeriesInfo

ASSOCIATED_TOKENS = ("label", "overview", "macro", "thumbnail", "thumb", "preview")


def classify_series(series: list[SeriesInfo]) -> list[SeriesInfo]:
    """Assign conservative WSI, associated-image, or unknown roles in place.

    Recognized reference-image names take precedence over dimensions. Large
    dimensions or explicit multiple-resolution metadata support WSI status;
    weak evidence remains ``UNKNOWN``.

    Args:
        series: Series records to classify. Their ``classification`` fields are
            updated in place.

    Returns:
        The same list, with each record's classification assigned.
    """
    pyramid_levels: set[int] = set()
    pyramid_parents: set[int] = set()
    for parent_pos, parent in enumerate(series):
        if not parent.width or not parent.height:
            continue
        for child in series[parent_pos + 1 :]:
            if not child.width or not child.height:
                continue
            if _is_pyramid_level(parent, child):
                pyramid_levels.add(child.index)
                pyramid_parents.add(parent.index)

    for item in series:
        metadata_text = " ".join(
            [item.name, *(str(value) for key, value in item.raw_metadata.items()
                          if key != "thumbnail_series")]
        ).casefold()
        is_thumbnail = item.raw_metadata.get("thumbnail_series") is True
        if is_thumbnail or any(token in metadata_text for token in ASSOCIATED_TOKENS):
            item.classification = SeriesClassification.ASSOCIATED_IMAGE
            item.classification_reason = "Image name or Bio-Formats metadata identifies an associated image."
        elif item.index in pyramid_levels:
            item.classification = SeriesClassification.PYRAMID_LEVEL
            item.classification_reason = "Dimensions match a downsampled level of a preceding image."
        elif item.width and item.height:
            if item.resolution_count is not None and item.resolution_count > 1:
                item.classification = SeriesClassification.WSI
                item.classification_reason = (
                    f"Bio-Formats reports {item.resolution_count} resolutions."
                )
            elif item.index in pyramid_parents:
                item.classification = SeriesClassification.WSI
                item.classification_reason = "Other inspected records match this image's pyramid levels."
            elif min(item.width, item.height) >= 5000:
                item.classification = SeriesClassification.WSI
                item.classification_reason = "Large dimensions support WSI classification; pyramid metadata is absent."
            else:
                item.classification = SeriesClassification.UNKNOWN
                item.classification_reason = "Metadata does not confidently identify a WSI or associated image."
        else:
            item.classification = SeriesClassification.UNKNOWN
            item.classification_reason = "Dimensions are unavailable."
    return series


def _is_pyramid_level(parent: SeriesInfo, child: SeriesInfo) -> bool:
    """Return whether ``child`` dimensions are a power-of-two level of ``parent``.

    Bio-Formats may expose every resolution of one VSI slide as a separate
    series. Allow a one-pixel rounding difference in either dimension.

    Args:
        parent: Candidate higher-resolution series.
        child: Candidate lower-resolution series.

    Returns:
        Whether both child dimensions match a halved parent resolution.
    """
    assert parent.width is not None and parent.height is not None
    assert child.width is not None and child.height is not None
    if child.width >= parent.width or child.height >= parent.height:
        return False
    scale = 2
    while scale <= 512:
        expected_width = round(parent.width / scale)
        expected_height = round(parent.height / scale)
        if abs(child.width - expected_width) <= 1 and abs(child.height - expected_height) <= 1:
            return True
        scale *= 2
    return False


def conversion_series_index(series: list[SeriesInfo], selected: SeriesInfo) -> int:
    """Map a displayed, flattened series to its source pyramid's base series.

    Some readers expose every resolution as an independent series during
    inspection, while Bio-Formats ``-noflat`` expects one index per base image.
    Count only non-pyramid entries that precede the selected series.

    Args:
        series: Ordered records returned by inspection.
        selected: Full-resolution series to convert.

    Returns:
        Zero-based Bio-Formats series index for conversion with ``-noflat``.

    Raises:
        SelectionError: If the selected record is not in the inspection list.
    """
    try:
        selected_position = series.index(selected)
    except ValueError as exc:
        raise SelectionError("Selected series is not present in the inspection results.") from exc
    return sum(
        item.classification != SeriesClassification.PYRAMID_LEVEL
        for item in series[:selected_position]
    )


def select_series(
    series: list[SeriesInfo], index: int | None = None, name: str | None = None
) -> SeriesInfo:
    """Select one eligible series by explicit identifier or confident inference.

    With no explicit identifier, only a single series already classified as
    ``WSI`` is selected. Ambiguous, missing, or associated-image selections
    raise ``SelectionError``.

    Args:
        series: Available records, usually returned by backend inspection.
        index: Source-specific numeric series index, if explicitly selected.
        name: Exact series name, if explicitly selected.

    Returns:
        The one selected series record.

    Raises:
        SelectionError: If both selectors are set, no unique match exists, an
            associated image is chosen, or inference is ambiguous.
    """
    if index is not None and name is not None:
        raise SelectionError("Specify either a series index or a series name, not both.")
    if index is not None:
        matches = [item for item in series if item.index == index]
        if matches:
            classification = matches[0].classification
            if classification == SeriesClassification.ASSOCIATED_IMAGE:
                raise SelectionError(f"Series {index} is classified as an associated image, not a WSI.")
            if classification == SeriesClassification.PYRAMID_LEVEL:
                raise SelectionError(
                    f"Series {index} is a downsampled pyramid level; select its full-resolution series instead."
                )
            return matches[0]
        raise SelectionError(f"Series index {index} was not found.")
    if name is not None:
        matches = [item for item in series if item.name == name]
        if len(matches) == 1:
            if matches[0].classification == SeriesClassification.ASSOCIATED_IMAGE:
                raise SelectionError(f"Series {name!r} is classified as an associated image, not a WSI.")
            if matches[0].classification == SeriesClassification.PYRAMID_LEVEL:
                raise SelectionError(
                    f"Series {name!r} is a downsampled pyramid level; select its full-resolution series instead."
                )
            return matches[0]
        raise SelectionError(f"Series name {name!r} was not found uniquely.")
    wsi = [item for item in series if item.classification == SeriesClassification.WSI]
    if len(wsi) == 1:
        return wsi[0]
    if len(wsi) > 1:
        raise SelectionError("Multiple WSI series were detected; explicit series selection is required.")
    raise SelectionError("No confidently classified WSI series; select a series explicitly.")


def select_wsi_series(series: list[SeriesInfo]) -> list[SeriesInfo]:
    """Return all confidently classified logical WSI images in source order.

    Associated images, pyramid levels, and unknown records are excluded.
    Unknown records remain visible in the inspection inventory and reports.

    Args:
        series: Classified logical-image inventory.

    Returns:
        Primary WSI records eligible for automatic conversion.

    Raises:
        SelectionError: If no primary WSI candidates were identified.
    """
    candidates = [item for item in series if item.classification == SeriesClassification.WSI]
    if not candidates:
        raise SelectionError("No confidently classified primary WSI images were found.")
    return candidates
