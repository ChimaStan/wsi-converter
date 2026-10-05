"""Bio-Formats inspection and pyramidal OME-TIFF conversion."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from ...exceptions import ConversionError, InspectionError
from ...models import ConversionResult, SeriesInfo
from ...series import classify_series
from .runner import BioFormatsRunner

logger = logging.getLogger(__name__)


class BioFormatsBackend:
    """Inspect Bio-Formats series and convert one selected series at a time."""

    def __init__(self, runner: BioFormatsRunner | None = None) -> None:
        """Use a provided command runner or create one with environment config.

        Args:
            runner: Optional runner responsible for executable discovery and
                subprocess execution. If omitted, paths are resolved lazily
                from CLI configuration, ``BIOFORMATS_HOME``, or ``PATH``.
        """
        self.runner = runner or BioFormatsRunner()

    def inspect(
        self, source: Path, *, flatten: bool = False, include_ome_xml: bool = False
    ) -> list[SeriesInfo]:
        """Inspect every series in ``source`` and return classified metadata.

        Args:
            source: Existing file to inspect with ``showinf``.

        Returns:
            Series records with parsed metadata and conservative role labels.

        Raises:
            InspectionError: If the source is missing, Bio-Formats fails, or
                its output contains no recognizable series information.
        """
        source = Path(source)
        if not source.is_file():
            raise InspectionError(f"Input file does not exist: {source}")
        text = self.runner.inspect(source, flatten=flatten, include_ome_xml=include_ome_xml)
        series = parse_showinf(text)
        if not series:
            raise InspectionError("Bio-Formats returned no recognizable series metadata.")
        logger.info("Bio-Formats found %d series in %s", len(series), source)
        return classify_series(series)

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
        """Convert one selected series to pyramidal OME-TIFF.

        The parent directory is created when needed. Existing outputs are
        protected unless ``overwrite`` is true; backend failures are captured
        in the returned result rather than raised.

        Args:
            source: Input container or WSI file; it is opened read-only.
            series: Source-specific series index returned by :meth:`inspect`.
            output: Destination path ending in ``.ome.tif`` or ``.ome.tiff``.
            overwrite: Whether Bio-Formats may replace an existing destination.
            conversion_series: Bio-Formats' unflattened index when it differs
                from the displayed, resolution-flattened series index.
            preserve_pyramid: Keep source resolutions together with ``-noflat``.
            compression: Optional Bio-Formats codec name, such as ``LZW``.
                ``None`` leaves output uncompressed by default.

        Returns:
            A ``ConversionResult`` containing the command, timestamps, and
            success or failure details.

        Raises:
            ConversionError: If the source or destination is invalid, or the
                destination exists while overwrite is disabled.
            OSError: If the output directory cannot be created.
        """
        source, output = Path(source), Path(output)
        if not source.is_file():
            raise ConversionError(f"Input file does not exist: {source}")
        if output.exists() and not overwrite:
            raise ConversionError(f"Output already exists; pass --overwrite to replace it: {output}")
        if not "".join(output.suffixes).lower().endswith((".ome.tif", ".ome.tiff")):
            raise ConversionError("Pyramidal OME-TIFF output must end in .ome.tif or .ome.tiff.")
        output.parent.mkdir(parents=True, exist_ok=True)
        started = _now()
        command: list[str] = []
        try:
            command, _ = self.runner.convert(
                source,
                series if conversion_series is None else conversion_series,
                output,
                overwrite,
                preserve_pyramid=preserve_pyramid,
                compression=compression,
            )
            if not output.is_file() or output.stat().st_size == 0:
                raise ConversionError("Bio-Formats returned success but did not create a non-empty output.")
            return ConversionResult(
                backend="bioformats", source=source, output=output, series=series,
                command=command, started_at=started, finished_at=_now(), success=True,
                backend_version=self.runner.version(),
            )
        except ConversionError as exc:
            return ConversionResult(
                backend="bioformats", source=source, output=output, series=series,
                command=command, started_at=started, finished_at=_now(), success=False,
                error=str(exc),
            )


def parse_showinf(text: str) -> list[SeriesInfo]:
    """Normalize common ``showinf`` text output into series records.

    Series indices are read from the output rather than inferred from order.
    Unsupported or absent metadata remains ``None`` for later conservative
    classification.

    Args:
        text: Combined textual output from a Bio-Formats ``showinf`` call.

    Returns:
        Parsed series in the order they appeared, retaining the reported IDs.
    """
    starts = list(re.finditer(
        r"(?im)^[ \t]*Series[ \t]*#?[ \t]*(\d+)[ \t]*:?[ \t]*(.*?)[ \t]*$", text
    ))
    if not starts:
        # Some Bio-Formats versions describe a single image without a series header.
        if not re.search(r"(?im)^\s*(?:Width|SizeX)\s*[:=]", text):
            return []
        blocks = [(0, "", text)]
    else:
        blocks = []
        for pos, match in enumerate(starts):
            end = starts[pos + 1].start() if pos + 1 < len(starts) else len(text)
            blocks.append((int(match.group(1)), match.group(2).strip(), text[match.end():end]))
    result: list[SeriesInfo] = []
    for index, header, body in blocks:
        name = _first(body, r"(?im)^\s*(?:Series name|Name)\s*[:=]\s*(.+?)\s*$")
        if name is None:
            name = header if not header.casefold().startswith("image count") else f"Series {index}"
        width = _number(body, r"(?:Width|SizeX)\s*[:=]\s*(\d+)")
        height = _number(body, r"(?:Height|SizeY)\s*[:=]\s*(\d+)")
        channels = _number(body, r"(?:Channels|SizeC)\s*[:=]\s*(\d+)")
        size_z = _number(body, r"(?:SizeZ|Z\s*sections)\s*[:=]\s*(\d+)")
        size_t = _number(body, r"(?:SizeT|Timepoints)\s*[:=]\s*(\d+)")
        resolutions = _number(body, r"(?:Resolution count|Resolutions)\s*[:=]\s*(\d+)")
        thumbnail = _first(body, r"(?im)^\s*Thumbnail series\s*[:=]\s*(true|false)\s*$")
        mpp_x = _decimal(body, r"(?:PhysicalSizeX|Pixel size X|X resolution)\s*[:=]\s*([\d.]+)")
        mpp_y = _decimal(body, r"(?:PhysicalSizeY|Pixel size Y|Y resolution)\s*[:=]\s*([\d.]+)")
        pixel_type = _first(body, r"(?im)^\s*(?:Type|Pixel type)\s*[:=]\s*(.+?)\s*$")
        dimension_order = _first(body, r"(?im)^\s*DimensionOrder\s*[:=]\s*(.+?)\s*$")
        result.append(SeriesInfo(
            index=index, name=name or f"Series {index}", width=width, height=height,
            axes=f"{dimension_order or ''};X={width},Y={height},C={channels},Z={size_z},T={size_t}",
            channels=channels, mpp_x=mpp_x, mpp_y=mpp_y,
            pixel_type=pixel_type, resolution_count=resolutions,
            raw_metadata={"thumbnail_series": thumbnail.casefold() == "true"} if thumbnail else {},
        ))
    _merge_ome_names(text, result)
    _merge_series_metadata(text, result)
    flat_offset = 0
    flat_index_known = True
    for item in result:
        item.flat_index = flat_offset if flat_index_known else None
        if item.resolution_count is None:
            flat_index_known = False
        else:
            flat_offset += item.resolution_count
    return result


def _merge_ome_names(text: str, series: list[SeriesInfo]) -> None:
    """Read logical image names from the OME-XML emitted by ``showinf``."""
    start = text.find("<OME")
    end = text.rfind("</OME>")
    if start < 0 or end < start:
        return
    try:
        root = ElementTree.fromstring(text[start : end + len("</OME>")])
    except ElementTree.ParseError:
        return
    by_index = {item.index: item for item in series}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Image":
            continue
        match = re.search(r"(\d+)$", element.attrib.get("ID", ""))
        name = element.attrib.get("Name")
        if match and name and int(match.group(1)) in by_index:
            item = by_index[int(match.group(1))]
            item.name = name
            item.raw_metadata["ome_image_name"] = name


def _merge_series_metadata(text: str, series: list[SeriesInfo]) -> None:
    """Attach reader metadata blocks to the matching source series record."""
    starts = list(re.finditer(r"(?im)^Reading series\s*#(\d+)\s+metadata\s*$", text))
    by_index = {item.index: item for item in series}
    for position, match in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(text)
        index = int(match.group(1))
        item = by_index.get(index)
        if item is None:
            continue
        for line in text[match.end() : end].splitlines():
            key, separator, value = line.partition(":")
            if separator:
                key, value = key.strip(), value.strip()
                if key and value:
                    item.raw_metadata[key] = value


def _number(text: str, pattern: str) -> int | None:
    """Return the first integer captured by ``pattern``, if present.

    Args:
        text: Metadata text to search.
        pattern: Regular expression with an integer capture group.

    Returns:
        Captured integer or ``None`` when no match exists.
    """
    match = re.search(pattern, text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _decimal(text: str, pattern: str) -> float | None:
    """Return the first decimal captured by ``pattern``, if present.

    Args:
        text: Metadata text to search.
        pattern: Regular expression with a numeric capture group.

    Returns:
        Captured float or ``None`` when no match exists.
    """
    match = re.search(pattern, text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _first(text: str, pattern: str) -> str | None:
    """Return the first trimmed text capture from ``pattern``.

    Args:
        text: Metadata text to search.
        pattern: Regular expression with a text capture group.

    Returns:
        Trimmed captured text or ``None`` when no match exists.
    """
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _now() -> str:
    """Return the current UTC timestamp in ISO 8601 format.

    Returns:
        Timezone-aware UTC timestamp string.
    """
    return datetime.now(timezone.utc).isoformat()
