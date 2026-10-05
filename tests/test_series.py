"""Unit tests for series classification and selection rules."""

import pytest

from wsi_converter.exceptions import SelectionError
from wsi_converter.models import SeriesClassification, SeriesInfo
from wsi_converter.series import (
    classify_series,
    conversion_series_index,
    select_series,
    select_wsi_series,
)


def test_series_classification_uses_evidence_and_is_conservative() -> None:
    """Recognize associated labels and large WSI data while retaining unknowns."""
    items = classify_series([
        SeriesInfo(index=0, name="label", width=400, height=300),
        SeriesInfo(index=1, name="tissue", width=12000, height=9000),
        SeriesInfo(index=2, name="small", width=400, height=300),
    ])

    assert [item.classification for item in items] == [
        SeriesClassification.ASSOCIATED_IMAGE, SeriesClassification.WSI, SeriesClassification.UNKNOWN
    ]


def test_multiple_wsi_series_are_all_eligible_for_automatic_processing() -> None:
    """Select every primary WSI instead of forcing a reader-specific index."""
    items = [
        SeriesInfo(index=1, name="one", classification=SeriesClassification.WSI),
        SeriesInfo(index=2, name="two", classification=SeriesClassification.WSI),
    ]

    assert [item.index for item in select_wsi_series(items)] == [1, 2]
    assert select_series(items, index=2).name == "two"


def test_automatic_series_selection_excludes_associated_and_unknown_records() -> None:
    """Keep ancillary and unresolved images out of automatic conversion."""
    items = classify_series([
        SeriesInfo(index=0, name="label", width=9000, height=9000, resolution_count=4),
        SeriesInfo(index=1, name="scan", width=12000, height=9000, resolution_count=5),
        SeriesInfo(index=2, name="unresolved", width=1200, height=900),
    ])

    assert [item.index for item in select_wsi_series(items)] == [1]


def test_vsi_pyramid_levels_are_not_classified_as_independent_slides() -> None:
    """Treat rounded half-resolution VSI series as pyramid levels of their root."""
    items = classify_series([
        SeriesInfo(index=0, name="slide", width=8021, height=9366),
        SeriesInfo(index=1, name="level 1", width=4011, height=4683),
        SeriesInfo(index=2, name="level 2", width=2006, height=2342),
        SeriesInfo(index=6, name="another slide", width=18032, height=9148),
        SeriesInfo(index=7, name="another level", width=9016, height=4574),
        SeriesInfo(index=13, name="tall slide", width=3680, height=23761),
        SeriesInfo(index=14, name="tall level", width=1840, height=11881),
    ])

    assert [item.classification for item in items] == [
        SeriesClassification.WSI,
        SeriesClassification.PYRAMID_LEVEL,
        SeriesClassification.PYRAMID_LEVEL,
        SeriesClassification.WSI,
        SeriesClassification.PYRAMID_LEVEL,
        SeriesClassification.WSI,
        SeriesClassification.PYRAMID_LEVEL,
    ]
    with pytest.raises(SelectionError, match="downsampled pyramid level"):
        select_series(items, index=2)


def test_unflattened_conversion_index_skips_pyramid_levels() -> None:
    """Map flattened VSI resolutions to Bio-Formats' base-series numbering."""
    items = classify_series([
        SeriesInfo(index=0, name="root 0", width=8000, height=8000),
        SeriesInfo(index=1, name="level 1", width=4000, height=4000),
        SeriesInfo(index=2, name="root 1", width=9000, height=9000),
        SeriesInfo(index=3, name="level 1", width=4500, height=4500),
        SeriesInfo(index=4, name="label", width=500, height=300),
    ])

    assert [conversion_series_index(items, item) for item in (items[0], items[2], items[4])] == [0, 1, 2]
