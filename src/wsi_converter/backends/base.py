"""Minimal backend protocol used by the conversion pipeline."""

from pathlib import Path
from typing import Protocol

from ..models import ConversionResult, SeriesInfo


class WSIBackend(Protocol):
    """Operations the pipeline needs from an inspection/conversion backend."""

    def inspect(
        self, source: Path, *, flatten: bool = False, include_ome_xml: bool = False
    ) -> list[SeriesInfo]:
        """Inspect a source and return normalized series metadata.

        Args:
            source: Existing multi-series image to inspect.
            flatten: Return separate records for each pyramid resolution.
            include_ome_xml: Include OME image names and metadata when true.

        Returns:
            Series metadata records preserving source-specific IDs.
        """
        ...

    def convert(
        self,
        source: Path,
        series: int,
        output: Path,
        overwrite: bool = False,
        conversion_series: int | None = None,
        preserve_pyramid: bool = True,
        compression: str | None = None,
    ) -> ConversionResult:
        """Convert one selected series and describe the command outcome.

        Args:
            source: Input image file.
            series: Source-specific series index.
            output: Destination path.
            overwrite: Whether an existing destination may be replaced.
            conversion_series: Optional index used by the converter when its
                series numbering differs from the inspection output.
            preserve_pyramid: Preserve source resolutions as sub-resolutions.
            compression: Optional Bio-Formats codec name; ``None`` means its
                default uncompressed output.

        Returns:
            Conversion status and provenance for the external command.
        """
        ...
