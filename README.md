# wsi_converter

`wsi_converter` assesses whole-slide image files through OpenSlide, inspects files that need alternative handling with Bio-Formats, converts logical WSI images to pyramidal OME-TIFF, and verifies each output independently through Bio-Formats and OpenSlide.

```text
CHECK → INSPECT → SELECT → CONVERT → VERIFY → REPORT
```

The actual file is tested. File extensions help directory discovery and diagnostics, but do not decide whether OpenSlide can use an image. Compatible sources can continue through an existing OpenSlide workflow without conversion.

## Python environment

For a local installation, use uv for the project interpreter, environment, and Python dependencies. The checked-in `.python-version` selects the project's Python version; `pyproject.toml` declares runtime dependencies, and `uv.lock` pins their resolved versions.

```bash
uv python install
uv sync --locked --no-dev
uv run wsi-converter --help
```

For development setup, tests, and quality checks, see [CONTRIBUTING.md](CONTRIBUTING.md).

`openslide-python` is the Python binding and `openslide-bin` supplies OpenSlide native binaries. They are distinct components, and both are declared as Python dependencies and pinned by `uv.lock`. On platforms with a compatible `openslide-bin` wheel, `uv sync` installs the native library that the binding loads. The `doctor` command checks the binding and native library separately. If a compatible wheel is unavailable and you use an OS-provided native library, record and configure that system dependency separately.

Outside the supplied Docker image, Bio-Formats and Java are external runtime prerequisites; they are not Python dependencies managed by uv. They can be found on `PATH`, configured with `BIOFORMATS_HOME`, or configured per command with `--bioformats-home`, `--bfconvert`, and `--showinf`. The Docker image below includes pinned Bio-Formats and Java versions.

The package does not require QuPath, Fiji, ImageJ, or a graphical environment. `uv.lock` pins the Python packages, including `openslide-bin`; it does not pin Java, Bio-Formats, or operating-system libraries.

## CLI reference

Use the built-in help for a concise command summary or complete option details:

```bash
wsi-converter --help
wsi-converter COMMAND --help
```

`--verbose` is a global option and must appear before the subcommand, for example `wsi-converter --verbose convert ...`.

### `check`

`wsi-converter check PATH...` assesses one or more files or directories with OpenSlide.

| Option | Description |
| --- | --- |
| `--recursive` | Search directories recursively. |
| `--output PATH` | Write `compatibility.json` and `compatibility.csv` under this directory. |
| `--require-pyramid` | Treat fewer than two OpenSlide levels as incompatible. |
| `--require-mpp` | Treat missing physical pixel-size metadata as incompatible. |
| `--json [PATH]` | Write detailed JSON to a file; without a path, print it to stdout. |
| `--csv [PATH]` | Write summary CSV to a file; without a path, print it to stdout. |

### `inspect`

`wsi-converter inspect PATH` inspects Bio-Formats series and metadata.

| Option | Description |
| --- | --- |
| `--json [PATH]` | Write inspection JSON to a file; without a path, print it to stdout. |
| `--bioformats-home PATH` | Bio-Formats command directory; defaults to `BIOFORMATS_HOME` or executable discovery on `PATH`. |
| `--bfconvert PATH` | Explicit path to `bfconvert`. |
| `--showinf PATH` | Explicit path to `showinf`. |

### `convert`

`wsi-converter convert PATH...` converts eligible WSI images. The default output root is `./converted/`; the default resolution mode is `preserve` and compression is uncompressed.

| Option | Description |
| --- | --- |
| `--output PATH` | Output root directory. |
| `--recursive` | Find supported input files recursively. |
| `--series ID` | Convert only the specified logical Bio-Formats series ID (advanced override). Mutually exclusive with `--series-name`. |
| `--series-name NAME` | Convert only the exact OME image name reported by `inspect`. Mutually exclusive with `--series`. |
| `--resolution-mode MODE` | Set `MODE` to `preserve` (default) or `flatten` to export one resolution. |
| `--compression METHOD` | Request a Bio-Formats compression method, such as `LZW`; omitted means uncompressed. |
| `--fallback-on-validation-failure` | Try the other resolution mode if conversion or validation fails. Disabled by default. |
| `--keep-partial-artifacts` | Retain partial output from a failed Bio-Formats process. |
| `--only-incompatible` | Skip sources that already pass the configured OpenSlide checks. |
| `--dry-run` | Inspect inputs and list planned outputs without converting. |
| `--overwrite` | Replace an existing accepted output at the selected mode's destination. |
| `--require-mpp` | Require physical pixel-size metadata during OpenSlide validation. |
| `--json [PATH]` | Write a live, atomically updated report; without a path, print final JSON to stdout. |
| `--bioformats-home PATH` | Bio-Formats command directory; defaults to `BIOFORMATS_HOME` or executable discovery on `PATH`. |
| `--bfconvert PATH` | Explicit path to `bfconvert`. |
| `--showinf PATH` | Explicit path to `showinf`. |

### `verify`

`wsi-converter verify PATH...` applies the shared OpenSlide checks to converted files.

| Option | Description |
| --- | --- |
| `--recursive` | Search directories recursively. |
| `--require-pyramid` | Require at least two OpenSlide pyramid levels. |
| `--require-mpp` | Require physical pixel-size metadata. |
| `--json [PATH]` | Write results to a file; without a path, print JSON to stdout. |

### `doctor`

`wsi-converter doctor` diagnoses Python and external runtime availability. It does not install or modify dependencies.

| Option | Description |
| --- | --- |
| `--json [PATH]` | Write diagnostics to a file; without a path, print JSON to stdout. |
| `--bioformats-home PATH` | Bio-Formats command directory; defaults to `BIOFORMATS_HOME` or executable discovery on `PATH`. |
| `--bfconvert PATH` | Explicit path to `bfconvert`. |
| `--showinf PATH` | Explicit path to `showinf`. |

### Additional Bio-Formats options

`wsi-converter` exposes the options listed for its commands; it does not currently accept arbitrary `bfconvert` or `showinf` flags. To see the options supported by your installed Bio-Formats version, run `bfconvert` without options. Bio-Formats also provides format-specific reader and writer options using `showinf -option KEY VALUE` and `bfconvert -option KEY VALUE`, respectively. Consult the [Bio-Formats command-line conversion guide](https://bio-formats.readthedocs.io/en/stable/users/comlinetools/conversion.html) and [reader and writer option reference](https://bio-formats.readthedocs.io/en/stable/formats/options.html) for syntax and applicability.

Running Bio-Formats directly bypasses `wsi-converter`'s conversion orchestration and report. Validate direct outputs independently, for example with `wsi-converter verify PATH` and, where relevant, `wsi-converter check PATH`.

## Assess OpenSlide compatibility

The examples use placeholder paths such as `path/to/wsis/`; replace them with paths on your system. Relative paths are resolved from the current working directory.

```bash
wsi-converter check path/to/wsis/slide.vsi
wsi-converter check path/to/wsis/ --recursive --output compatibility/
wsi-converter check path/to/wsis/slide.tif --require-pyramid --require-mpp
```

The checker opens the image, captures format and pyramid metadata, and reads small regions at the upper left, centre, lower right, and a representative pyramid level when available. Requirements can be configured: opening, dimensions, pyramid, representative reads, MPP, and associated images. The default requires a successful open, usable dimensions, and representative reads; MPP absence is reported as a warning. Use `--require-mpp` or `--require-pyramid` when your analysis needs those capabilities.

Batch `check` returns exit code 0 when assessment completes, including when some files are incompatible or unreadable. A checker or command failure returns non-zero. Reports include the per-file result and summary counts. `--output` writes `compatibility.json` and `compatibility.csv`; `--json [PATH]` and `--csv [PATH]` can write either report directly (omit the path to print to stdout).

## Inspect series

Inspection uses Bio-Formats' logical, unflattened series inventory and OME-XML names. It reports dimensions, pyramid resolution counts, classification evidence, and associated images:

```bash
wsi-converter inspect path/to/wsis/sample.vsi
wsi-converter inspect path/to/wsis/sample.vsi --json series.json
```

You can provide an explicit Bio-Formats installation directory when it is not on `PATH`:

```bash
uv run wsi-converter inspect path/to/wsis/sample.vsi --bioformats-home path/to/bioformats
```

Series IDs are specific to a source file. `label`, `overview`, `macro`, and thumbnail images are reported as associated images. Pyramid levels remain grouped with their logical image rather than appearing as selectable images. Unknown images remain visible in inspection reports and are not silently converted.

## Convert and verify

```bash
wsi-converter inspect path/to/wsis/sample.vsi
wsi-converter convert path/to/wsis/sample.vsi \
  --output path/to/converted/
```

Without a selector, conversion processes every confidently identified primary WSI image in the file, each as an independent output. Associated images and unresolved records are reported and excluded. This avoids choosing an arbitrary “first” or “largest” series. To convert one image only, use its exact logical name or ID from `inspect`:

```bash
wsi-converter convert path/to/wsis/sample.vsi \
  --series-name 'Example primary WSI series' --output path/to/converted/
```

Replace `Example primary WSI series` with the exact logical image name shown by `inspect` for your input file.

`--series` is an advanced numeric override; `--series-name` uses the OME image name exposed by `inspect`. Pyramid-preserving outputs are placed under `<output>/<source-name>/wsi/`. Explicitly flattened exports are placed under `<output>/<source-name>/flattened/`. Names contain the logical image name and source ID. Existing outputs are protected unless `--overwrite` is supplied.

Conversion defaults to `--resolution-mode preserve`, which passes Bio-Formats `-noflat` and requires a matching pyramid from both validators. `--resolution-mode flatten` explicitly exports one flattened resolution; that mode checks matching dimensions and readability without claiming the result is a pyramidal WSI. There is no automatic mode switch. `--fallback-on-validation-failure` opts into trying the other mode after the requested mode fails.

Bio-Formats writes OME-TIFF as BigTIFF with 64-bit offsets so large uncompressed WSI planes remain readable by OpenSlide. This affects the TIFF container structure; pixel compression is not enabled.

Compression is optional and is disabled by default. To request a Bio-Formats codec, pass its method name, for example `--compression LZW`. Supported methods depend on the Bio-Formats version and OME-TIFF writer; Bio-Formats reports an error if the selected method is unavailable. Lossy codecs can change pixel values, so choose a method appropriate to the intended downstream analysis. The selected method (or `uncompressed`) is recorded in the conversion report.

Each conversion attempt is staged and checked independently. Outputs from completed Bio-Formats conversions are retained under `<output>/<source-name>/unverified/` when validation rejects them. These are diagnostic artifacts and are not accepted outputs. Non-empty files left by a failed Bio-Formats process are discarded by default; `--keep-partial-artifacts` retains them in the same directory with a `partial` label. Only outputs passing the checks required by the selected mode go to `wsi/` or `flattened/`.

```bash
wsi-converter convert path/to/wsis/ --recursive --only-incompatible \
  --output path/to/converted/ --dry-run
```

`--only-incompatible` bases its decision on actual OpenSlide assessment. A dry run reports every planned logical image and output path without converting. Recursive conversion isolates failures by image and continues with remaining images and files. Use `--json PATH` to write a live, atomically updated hierarchical report: run → source → logical WSI → attempt → independent checks and artifact disposition. `passed` means every eligible image passed its selected mode's required checks; `partial` means at least one attempt or image failed while work completed; `failed` means no conversion completed successfully. `planned` and `skipped` distinguish dry runs and runs with no conversion work. The report includes per-source and per-image counts. Bare `--json` prints the final JSON to stdout; progress remains visible in the terminal.

The conversion JSON is intentionally compact: its source inventory includes series identity, role, dimensions, resolution count, and calibration where available, but omits raw vendor metadata and full process output. Use `inspect --json PATH` when you need the detailed Bio-Formats inventory and metadata.

Run a standalone verification:

```bash
wsi-converter verify path/to/converted/slide.ome.tiff --require-pyramid
```

During conversion, an output is reported as verified only if Bio-Formats confirms matching dimensions and pyramid structure and OpenSlide opens it with required pyramid and pixel-read checks passing. The standalone `verify` command runs OpenSlide checks; it does not repeat Bio-Formats validation. An OME-TIFF extension alone does not guarantee OpenSlide compatibility: OpenSlide documents generic tiled TIFF support when the initial image is tiled and lower-resolution tiled images are marked as reduced-resolution levels. It does not list OME-TIFF as a separately guaranteed format. [OpenSlide supported formats](https://openslide.org/formats/) and [generic tiled TIFF requirements](https://openslide.org/formats/generic-tiff/).

## Reports and reproducibility

JSON captures structured checks, metadata, warnings, errors, series selection, conversion parameters, tool version where available, timestamps, output path, and verification result. CSV is a compact per-file summary for spreadsheet and batch workflows. Reports avoid extracting unnecessary clinical metadata.

## Troubleshooting

* **OpenSlide binding or native library missing:** run `uv sync --locked` to install the declared `openslide-python` and `openslide-bin` packages. If no compatible `openslide-bin` wheel exists for your platform, install a system OpenSlide library and check the environment again with `wsi-converter doctor`.
* **Bio-Formats executable missing:** install the Bio-Formats command-line tools, add them to `PATH`, set `BIOFORMATS_HOME`, or pass the executable options.
* **VSI companion-directory warning:** some `.vsi` files use a neighbouring directory named from the source file, such as `_sample_/` alongside `sample.vsi`. The checker looks for that directory and reports a warning if it is missing; this does not by itself mean the image is invalid, and inspection is still attempted. This note applies to VSI files only; other formats may have different file-layout requirements.
* **Corrupt/unreadable image:** inspect the recorded OpenSlide errors and try Bio-Formats inspection where appropriate.
* **Unknown image role:** inspect the OME image name, dimensions, resolution count, and classification evidence. Unknown records are reported and excluded from automatic conversion; use `--series` or `--series-name` only when you have identified the intended logical image.
* **Output exists:** choose another output directory or explicitly pass `--overwrite`.
* **Verification fails:** the converted file remains unverified; check the output assessment in JSON and the Bio-Formats diagnostics, then review conversion parameters and tool versions.

## Environment diagnostics

Run the read-only dependency check inside the uv environment:

```bash
uv run wsi-converter doctor
uv run wsi-converter doctor --bioformats-home path/to/bioformats
```

It reports Python and package versions/locations, distinguishes the `openslide-python` binding from the native OpenSlide library, checks `bfconvert` and `showinf`, and probes Java and Bio-Formats versions where possible. It reports missing components and does not install, update, or configure software.

## Container use

The Docker image is published to the [wsi-converter GHCR package](https://github.com/users/ChimaStan/packages/container/package/wsi-converter). The package page lists available tags and access details; it will show the image after the first release is published. Mount your WSI input read-only and choose an output directory writable by the container user. The image's `BIOFORMATS_HOME` is configured internally; users can override it by mounting another Bio-Formats installation and setting the container path.

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --volume /path/to/wsis:/input:ro \
  --volume /path/to/converted:/output \
  ghcr.io/chimastan/wsi-converter:0.1.0 \
  convert /input --recursive --output /output --json /output/report.json
```

Replace the absolute host mount paths and image version with values appropriate for your system. The paths `/input` and `/output` are inside the container. For an alternate Bio-Formats release, mount its tools directory and set `BIOFORMATS_HOME`, for example `--volume /path/to/other-bioformats:/external/bioformats:ro --env BIOFORMATS_HOME=/external/bioformats`.

See [HPC and deployment](#hpc-and-deployment) for Apptainer use and runtime recording. To build or publish the image, see [CONTRIBUTING.md](CONTRIBUTING.md).

## HPC and deployment

For reproducible deployments, pin the container image by digest and record Java, Bio-Formats, and base OS versions separately from `uv.lock`. The lockfile covers the Python environment, including the `openslide-bin` native OpenSlide distribution. For environments using a system-provided OpenSlide library instead, record that native library separately. The package works in headless Docker and Apptainer/Singularity jobs; Bio-Formats can be included in the image or mounted and configured through `BIOFORMATS_HOME`, `--bioformats-home`, `--bfconvert`, or `--showinf`.

Example Apptainer invocation when Bio-Formats is supplied from a mounted directory:

```bash
apptainer exec --bind /path/to/bioformats:/opt/bioformats \
  --env BIOFORMATS_HOME=/opt/bioformats path/to/wsi-converter.sif \
  uv run wsi-converter doctor
```

Record at least the Java vendor/version/path, Bio-Formats release and executable paths, OpenSlide native version/library path, and container image digest/base OS release with each deployment. Use the separate [external runtime record](docs/external-runtime.md) for deployment details.

## Acknowledgements and citation

This project uses [Bio-Formats](https://www.openmicroscopy.org/bio-formats/), developed by the Open Microscopy Environment. Its [recommended citation](https://bio-formats.readthedocs.io/en/stable/about/index.html) is:

> Melissa Linkert, Curtis T. Rueden, Chris Allan, Jean-Marie Burel, Will Moore, Andrew Patterson, Brian Loranger, Josh Moore, Carlos Neves, Donald MacDonald, Aleksandra Tarkowska, Caitlin Sticco, Emma Hill, Mike Rossner, Kevin W. Eliceiri, and Jason R. Swedlow (2010). Metadata matters: access to image data in the real world. *The Journal of Cell Biology*, 189(5), 777–782. https://doi.org/10.1083/jcb.201004104
