"""Deterministic discovery of candidate WSI files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import InputError

WSI_EXTENSIONS = {
    ".vsi", ".svs", ".ndpi", ".mrxs", ".scn", ".czi", ".lif", ".nd2",
    ".tif", ".tiff", ".ome.tif", ".ome.tiff", ".bif", ".qptiff", ".isyntax",
    ".dcm", ".dicom", ".jp2", ".png", ".jpg", ".jpeg",
}


@dataclass
class DiscoveryResult:
    """Candidate files and path-level problems found during discovery.

    Attributes:
        files: Unique candidate paths in deterministic order.
        inaccessible: Human-readable explanations for paths that could not be
            inspected or traversed.
    """

    files: list[Path] = field(default_factory=list)
    inaccessible: list[str] = field(default_factory=list)


def _is_candidate(path: Path) -> bool:
    """Return whether a filename is eligible for directory-based discovery.

    Args:
        path: File path whose suffixes should be checked.

    Returns:
        ``True`` for a known WSI-like suffix; this does not imply compatibility.
    """
    suffixes = "".join(path.suffixes).lower()
    return path.suffix.lower() in WSI_EXTENSIONS or suffixes.endswith((".ome.tif", ".ome.tiff"))


def discover(paths: list[Path], recursive: bool = False) -> DiscoveryResult:
    """Find candidate files without opening them or judging their compatibility.

    Explicit files are included regardless of extension. Directory traversal is
    filtered by known WSI-like suffixes, deduplicated by resolved path, and
    sorted deterministically. Paths that cannot be inspected are recorded in
    ``DiscoveryResult.inaccessible``.

    Args:
        paths: Files and directories supplied by the caller.
        recursive: Whether to descend into child directories.

    Returns:
        A deterministic, de-duplicated candidate list and any inaccessible
        paths. Explicit files are not filtered by suffix.

    Raises:
        InputError: If no input paths were provided.
    """
    found: set[Path] = set()
    inaccessible: list[str] = []
    if not paths:
        raise InputError("Provide at least one input file or directory.")
    for raw_path in paths:
        path = raw_path.expanduser()
        try:
            if not path.exists():
                inaccessible.append(f"{path}: does not exist")
            elif path.is_file():
                found.add(path.resolve())
            elif path.is_dir():
                try:
                    iterator = path.rglob("*") if recursive else path.iterdir()
                    for candidate in iterator:
                        try:
                            if candidate.is_file() and _is_candidate(candidate):
                                found.add(candidate.resolve())
                        except OSError as exc:
                            inaccessible.append(f"{candidate}: {exc}")
                except OSError as exc:
                    inaccessible.append(f"{path}: {exc}")
            else:
                inaccessible.append(f"{path}: not a regular file or directory")
        except OSError as exc:
            inaccessible.append(f"{path}: {exc}")
    return DiscoveryResult(sorted(found, key=lambda item: str(item).casefold()), sorted(inaccessible))
