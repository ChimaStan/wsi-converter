"""The sole subprocess boundary for Bio-Formats tools."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ...exceptions import BackendError, ConversionError, InspectionError

logger = logging.getLogger(__name__)


class BioFormatsRunner:
    """Resolve and execute Bio-Formats command-line tools.

    Resolution is lazy so inspection-only use does not require ``bfconvert``
    and conversion-only code paths do not resolve tools before needed.
    """

    def __init__(
        self,
        bfconvert: str | Path | None = None,
        showinf: str | Path | None = None,
        timeout: float | None = None,
        bioformats_home: str | Path | None = None, # bftools/ can be downloaded from https://downloads.openmicroscopy.org/bio-formats/
    ) -> None:
        """Store paths and settings without probing or launching external tools.

        Args:
            bfconvert: Explicit executable path, if supplied.
            showinf: Explicit executable path, if supplied.
            timeout: Maximum seconds allowed for each child process; ``None``
                allows it to run without a timeout.
            bioformats_home: Installation root containing the command scripts,
                or ``None`` to consult ``BIOFORMATS_HOME`` and then ``PATH``.
        """
        self._bfconvert_config = bfconvert
        self._showinf_config = showinf
        self.timeout = timeout
        self.bioformats_home = Path(bioformats_home).expanduser() if bioformats_home else None

    @property
    def bfconvert(self) -> str:
        """Resolve the configured ``bfconvert`` executable.

        Returns:
            Resolved executable path.
        """
        return self.resolve_executable(self._bfconvert_config, "bfconvert", self.bioformats_home)

    @property
    def showinf(self) -> str:
        """Resolve the configured ``showinf`` executable.

        Returns:
            Resolved executable path.
        """
        return self.resolve_executable(self._showinf_config, "showinf", self.bioformats_home)

    @staticmethod
    def resolve_executable(
        configured: str | Path | None,
        name: str,
        bioformats_home: str | Path | None = None,
    ) -> str:
        """Find a tool by explicit path, install root, or ``PATH``.

        An explicit executable or installation root is authoritative: if it
        does not contain the requested program, resolution fails instead of
        silently selecting a different PATH installation.

        Args:
            configured: Explicit executable path, or ``None`` to use other
                discovery sources.
            name: Executable basename, normally ``bfconvert`` or ``showinf``.
            bioformats_home: Optional installation root; environment
                configuration is consulted when this is omitted.

        Returns:
            Resolved executable path suitable for subprocess argument lists.

        Raises:
            BackendError: If an explicit path/root is invalid or no executable
                can be found.
        """
        candidates: list[str | Path] = []
        if configured:
            value = str(configured)
            located = shutil.which(value)
            if located:
                return located
            if Path(value).is_file():
                return str(Path(value).resolve())
            raise BackendError(f"Configured Bio-Formats executable does not exist: {configured}")
        home = Path(bioformats_home).expanduser() if bioformats_home else None
        if home is None and os.environ.get("BIOFORMATS_HOME"):
            home = Path(os.environ["BIOFORMATS_HOME"]).expanduser()
        if home:
            candidates.extend([Path(home) / name, Path(home) / "bin" / name])
            if os.name == "nt":
                for extension in (".exe", ".bat", ".cmd"):
                    candidates.extend([
                        Path(home) / f"{name}{extension}",
                        Path(home) / "bin" / f"{name}{extension}",
                    ])
        for candidate in candidates:
            value = str(candidate)
            located = shutil.which(value)
            if located:
                return located
            if Path(value).is_file():
                return str(Path(value).resolve())
        if home is not None:
            raise BackendError(f"Bio-Formats executable {name} was not found under BIOFORMATS_HOME={home}")
        located = shutil.which(name)
        if located:
            return located
        raise BackendError(
            f"Bio-Formats executable not found: {name}. Configure --{name}, "
            "--bioformats-home, or BIOFORMATS_HOME."
        )

    def inspect(
        self, source: Path, *, flatten: bool = False, include_ome_xml: bool = False
    ) -> str:
        """Run ``showinf`` without pixel output and return captured text.

        Args:
            source: File to inspect.
            flatten: Keep pyramid levels as separate series when true.
            include_ome_xml: Ask Bio-Formats to emit logical image names.

        Returns:
            Combined stdout and stderr from a successful inspection.

        Raises:
            InspectionError: If the tool is missing, times out, or exits
                unsuccessfully.
        """
        try:
            executable = self.showinf
        except BackendError as exc:
            raise InspectionError(str(exc)) from exc
        command = [executable, "-nopix", "-no-upgrade"]
        if not flatten:
            command.append("-noflat")
        if include_ome_xml:
            command.append("-omexml")
        command.append(str(source))
        return self._run(command, InspectionError)

    def convert(
        self,
        source: Path,
        series: int,
        output: Path,
        overwrite: bool = False,
        *,
        preserve_pyramid: bool = True,
        compression: str | None = None,
    ) -> tuple[list[str], str]:
        """Run ``bfconvert`` for one series and return argv plus captured text.

        Args:
            source: File containing the requested series.
            series: Bio-Formats series index after pyramid levels are kept unflattened.
            output: Destination OME-TIFF path.
            overwrite: Whether the command may replace an existing output.
            preserve_pyramid: Pass ``-noflat`` to keep source resolutions together.
            compression: Optional Bio-Formats compression name, such as ``LZW``.
                ``None`` leaves Bio-Formats' default uncompressed output.

        Returns:
            Pair containing the exact argument vector and command output.

        Raises:
            ConversionError: If the executable is unavailable or conversion
                fails, times out, or cannot be launched.
        """
        try:
            executable = self.bfconvert
        except BackendError as exc:
            raise ConversionError(str(exc)) from exc
        # Force BigTIFF for large uncompressed WSI planes. Classic TIFF uses
        # 32-bit offsets and is not reliably readable by OpenSlide once these
        # outputs exceed the 2 GiB range encountered in practice.
        command = [executable, "-no-upgrade", "-bigtiff"]
        if preserve_pyramid:
            command.append("-noflat")
        if compression is not None:
            method = compression.strip()
            if not method:
                raise ConversionError("Compression method cannot be empty.")
            command.extend(["-compression", method])
        if overwrite:
            command.append("-overwrite")
        command.extend(["-series", str(series), str(source), str(output)])
        return command, self._run(command, ConversionError)

    def version(self) -> str | None:
        """Return the ``showinf`` version banner when it can be queried.

        Returns:
            Version text, or ``None`` when the probe fails.
        """
        try:
            return self._run([self.showinf, "-version"], InspectionError).strip() or None
        except BackendError:
            return None

    def _run(self, command: list[str], error_type: type[BackendError]) -> str:
        """Execute one command and translate process failures to domain errors.

        Args:
            command: Argument vector passed directly to ``subprocess.run``.
            error_type: Domain exception class used for process failures.

        Returns:
            Combined output from a command that exits successfully.

        Raises:
            error_type: When launch, timeout, or exit status indicates failure.
        """
        logger.info("Running Bio-Formats command: %s", command)
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError as exc:
            raise error_type(f"Bio-Formats executable not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise error_type(f"Bio-Formats command timed out: {command[0]}") from exc
        except OSError as exc:
            raise error_type(f"Could not run Bio-Formats command {command[0]}: {exc}") from exc
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0:
            excerpt = output[-2000:] if output else "no diagnostic output"
            raise error_type(f"Bio-Formats exited with status {completed.returncode}: {excerpt}")
        return output
