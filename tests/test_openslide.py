"""Unit tests for OpenSlide metadata checks and representative reads."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from wsi_converter.models import AssessmentStatus, OpenSlideRequirements
from wsi_converter.openslide import OpenSlideChecker


class FakeRegion:
    """Minimal image region exposing the pixel access used by the checker."""

    def getpixel(self, point: tuple[int, int]) -> tuple[int, int, int]:
        """Return a stable sample pixel for a representative read."""
        return (1, 2, 3)


class FakeSlide:
    """Small deterministic OpenSlide stand-in for checker unit tests."""

    properties: dict[str, str]
    dimensions = (1000, 800)
    level_count = 2
    level_dimensions: ClassVar[list[tuple[int, int]]] = [(1000, 800), (500, 400)]
    level_downsamples: ClassVar[list[float]] = [1.0, 2.0]
    associated_images: ClassVar[dict[str, FakeRegion]] = {}

    def __init__(self) -> None:
        """Initialize an empty record of region reads."""
        self.properties = {
            "openslide.vendor": "test",
            "openslide.mpp-x": "0.25",
            "openslide.mpp-y": "0.25",
        }
        self.reads: list[tuple[tuple[int, int], int, tuple[int, int]]] = []

    def read_region(
        self, location: tuple[int, int], level: int, size: tuple[int, int]
    ) -> FakeRegion:
        """Record the requested region and return a readable stand-in."""
        self.reads.append((location, level, size))
        return FakeRegion()

    def close(self) -> None:
        """Match the OpenSlide resource cleanup method."""
        return


def test_checker_reads_representative_regions_and_reports_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check metadata and all configured representative regions on success."""
    image = tmp_path / "slide.tif"
    image.touch()
    slide = FakeSlide()
    fake_module = SimpleNamespace(
        OpenSlide=lambda path: slide,
        __library_version__="test-version",
    )
    monkeypatch.setitem(sys.modules, "openslide", fake_module)

    assessment = OpenSlideChecker().check(image)

    assert assessment.status == AssessmentStatus.COMPATIBLE
    assert assessment.detected_format == "test"
    assert assessment.level_count == 2
    assert len(slide.reads) == 4
    assert next(test for test in assessment.tests if test.name == "tile_reads").status == "PASS"


def test_required_mpp_is_an_incompatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing required physical pixel size should fail the assessment."""
    image = tmp_path / "slide.tif"
    image.touch()
    slide = FakeSlide()
    slide.properties = {"openslide.vendor": "test"}
    monkeypatch.setitem(sys.modules, "openslide", SimpleNamespace(OpenSlide=lambda path: slide))

    result = OpenSlideChecker(OpenSlideRequirements(require_mpp=True)).check(image)

    assert result.status == AssessmentStatus.INCOMPATIBLE
    assert any("mpp" in error for error in result.errors)
