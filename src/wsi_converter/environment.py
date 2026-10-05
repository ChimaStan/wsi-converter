"""Read-only environment diagnostics for Python and external runtimes."""

from __future__ import annotations

import ctypes.util
import importlib.metadata
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__
from .backends.bioformats.runner import BioFormatsRunner
from .exceptions import BackendError

logger = logging.getLogger(__name__)


@dataclass
class Diagnostic:
    """Availability result with optional version, location, and explanation.

    Attributes:
        name: Dependency or environment component being checked.
        passed: Whether the diagnostic considers the component available.
        location: Resolved executable, module, or library location when known.
        version: Detected version string when available.
        detail: Human-readable supplementary evidence or failure reason.
    """

    name: str
    passed: bool
    location: str | None = None
    version: str | None = None
    detail: str = ""

    @property
    def status(self) -> str:
        """Return the display label corresponding to ``passed``.

        Returns:
            ``PASS`` when the check passed, otherwise ``FAIL``.
        """
        return "PASS" if self.passed else "FAIL"


def diagnose_environment(
    bioformats_home: Path | None = None,
    bfconvert: Path | None = None,
    showinf: Path | None = None,
) -> list[Diagnostic]:
    """Probe Python and external runtimes without changing the environment.

    Executable version checks are bounded by a short timeout. Bio-Formats can
    be resolved using explicit executable paths, an install root, environment
    configuration, or ``PATH``; Java is checked on ``PATH`` and under
    ``JAVA_HOME``.

    Args:
        bioformats_home: Optional installation root, overriding the environment
            variable for these checks.
        bfconvert: Optional explicit path to the ``bfconvert`` command.
        showinf: Optional explicit path to the ``showinf`` command.

    Returns:
        Ordered diagnostics for Python, the package, OpenSlide, Bio-Formats,
        its commands, and Java. Missing dependencies are represented as failed
        checks instead of exceptions.
    """
    diagnostics = [
        Diagnostic("Python", True, sys.executable, sys.version.split()[0], "Active interpreter"),
        Diagnostic("wsi_converter", True, _package_location(), __version__, "Importable package"),
    ]

    openslide_spec = importlib.util.find_spec("openslide")
    diagnostics.append(Diagnostic(
        "OpenSlide Python", openslide_spec is not None,
        str(openslide_spec.origin) if openslide_spec and openslide_spec.origin else None,
        _distribution_version("openslide-python") if openslide_spec else None,
        "openslide-python bindings" if openslide_spec else "Install with uv sync.",
    ))
    diagnostics.append(_native_openslide_check(openslide_spec is not None))

    try:
        bfconvert_path = BioFormatsRunner.resolve_executable(bfconvert, "bfconvert", bioformats_home)
    except BackendError as exc:
        bfconvert_path = None
        bfconvert_error = str(exc)
    else:
        bfconvert_error = "Executable found"
    try:
        showinf_path = BioFormatsRunner.resolve_executable(showinf, "showinf", bioformats_home)
    except BackendError as exc:
        showinf_path = None
        showinf_error = str(exc)
    else:
        showinf_error = "Executable found"

    java_path = shutil.which("java") or _java_from_home()
    java_version, java_error = (
        _command_version([java_path, "-version"])
        if java_path
        else (None, "java not found on PATH or JAVA_HOME")
    )
    bfconvert_version = _binary_version(bfconvert_path)
    showinf_version = _binary_version(showinf_path)
    diagnostics.extend([
        Diagnostic("Bio-Formats", bool(bfconvert_path and showinf_path),
                   str(bioformats_home or os.environ.get("BIOFORMATS_HOME") or "PATH"),
                   showinf_version,
                   "Both command-line tools are available" if bfconvert_path and showinf_path
                   else "Both bfconvert and showinf are required."),
        Diagnostic("bfconvert", bfconvert_path is not None, bfconvert_path,
                   bfconvert_version, bfconvert_error),
        Diagnostic("showinf", showinf_path is not None, showinf_path,
                   showinf_version, showinf_error),
        Diagnostic("Java", java_path is not None and java_version is not None,
                   java_path, java_version, java_error),
    ])
    return diagnostics


def _native_openslide_check(binding_available: bool) -> Diagnostic:
    """Check native library loading separately from the Python binding.

    Args:
        binding_available: Whether Python's module finder located
            ``openslide-python``.

    Returns:
        Native library availability, path, version when available, and detail.
    """
    if not binding_available:
        native = ctypes.util.find_library("openslide")
        if native:
            try:
                ctypes.CDLL(native)
                return Diagnostic("OpenSlide native", True, native, None,
                                  "Native library loaded; install openslide-python to use it from Python.")
            except OSError as exc:
                return Diagnostic("OpenSlide native", False, native, None, str(exc))
        return Diagnostic("OpenSlide native", False, None, None,
                          "Native library was not found; openslide-python is also missing.")
    try:
        import openslide

        version = getattr(openslide, "__library_version__", None)
        library_path = None
        try:
            from openslide import lowlevel

            library_path = str(getattr(lowlevel._lib, "_name", "")) or None
        except (AttributeError, ImportError, OSError):
            library_path = ctypes.util.find_library("openslide")
        return Diagnostic(
            "OpenSlide native", bool(version), library_path, str(version) if version else None,
            "Native library loaded" if version else "Python bindings loaded but native version is unavailable.",
        )
    except (ImportError, OSError) as exc:
        return Diagnostic("OpenSlide native", False, ctypes.util.find_library("openslide"), None, str(exc))


def _package_location() -> str:
    """Return the resolved source location of the imported application.

    Returns:
        Absolute module path, or a diagnostic string if it cannot be imported.
    """
    try:
        import wsi_converter

        return str(Path(wsi_converter.__file__).resolve())
    except (ImportError, AttributeError):
        return "not importable"


def _distribution_version(name: str) -> str | None:
    """Return an installed Python distribution version, if it is registered.

    Args:
        name: Distribution name used by Python package metadata.

    Returns:
        Installed version string, or ``None`` if not registered.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _binary_version(executable: str | None) -> str | None:
    """Query one external executable for its version banner when available.

    Args:
        executable: Resolved command path, or ``None`` if not found.

    Returns:
        First line of the version output, or ``None`` if unavailable.
    """
    if not executable:
        return None
    output, _ = _command_version([executable, "-version"])
    return output


def _java_from_home() -> str | None:
    """Resolve the Java executable from ``JAVA_HOME`` for the current OS.

    Returns:
        Absolute executable path if it exists; otherwise ``None``.
    """
    home = os.environ.get("JAVA_HOME")
    if not home:
        return None
    suffix = ".exe" if os.name == "nt" else ""
    executable = Path(home) / "bin" / f"java{suffix}"
    return str(executable.resolve()) if executable.is_file() else None


def _command_version(command: list[str]) -> tuple[str | None, str]:
    """Run a read-only version probe and return its first line and diagnosis.

    Args:
        command: Executable and arguments, normally ending in ``-version``.

    Returns:
        Pair of version text (or ``None``) and a success/failure explanation.
    """
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Version probe failed for %s", command[0], exc_info=True)
        return None, str(exc)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        return None, output or f"Exited with status {result.returncode}"
    first_line = output.splitlines()[0] if output else "version not reported"
    return first_line, "Version command succeeded"


def diagnostics_to_dict(diagnostics: list[Diagnostic]) -> list[dict[str, object]]:
    """Convert diagnostics to dictionaries suitable for JSON reporting.

    Args:
        diagnostics: Ordered checks returned by :func:`diagnose_environment`.

    Returns:
        JSON-compatible dictionaries including each computed PASS/FAIL label.
    """
    return [asdict(item) | {"status": item.status} for item in diagnostics]
