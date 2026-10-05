# wsi_converter

`wsi_converter` assesses whole-slide image files through OpenSlide, inspects files that need alternative handling with Bio-Formats, converts logical WSI images to pyramidal OME-TIFF, and verifies each output independently through Bio-Formats and OpenSlide.

```text
CHECK → INSPECT → SELECT → CONVERT → VERIFY → REPORT
```

The actual file is tested. File extensions help directory discovery and diagnostics, but do not decide whether OpenSlide can use an image. Compatible sources can continue through an existing OpenSlide workflow without conversion.

## Python environment

Use uv for the project interpreter, environment, Python dependencies, and developer tools. The checked-in `.python-version` selects the project's Python version; `pyproject.toml` declares runtime and development dependencies, and `uv.lock` is intended to pin the resolved Python environment.

```bash
uv python install
uv sync
uv run wsi-converter --help
uv run pytest
uv run ruff check .
uv run mypy .
```

Run `uv lock` after changing dependency declarations and commit the resulting `uv.lock`. For deployments that must reject an out-of-date or missing lockfile, use `uv sync --locked`.

`openslide-python` is the Python binding and `openslide-bin` supplies OpenSlide native binaries. They are distinct components, and both are declared as Python dependencies and pinned by `uv.lock`. On platforms with a compatible `openslide-bin` wheel, `uv sync` installs the native library that the binding loads. The `doctor` command checks the binding and native library separately. If a compatible wheel is unavailable and you use an OS-provided native library, record and configure that system dependency separately.

Outside the supplied Docker image, Bio-Formats and Java are external runtime prerequisites; they are not Python dependencies managed by uv. They can be found on `PATH`, configured with `BIOFORMATS_HOME`, or configured per command with `--bioformats-home`, `--bfconvert`, and `--showinf`. The Docker image below includes pinned Bio-Formats and Java versions.

The package does not require QuPath, Fiji, ImageJ, or a graphical environment. `uv.lock` pins the Python packages, including `openslide-bin`; it does not pin Java, Bio-Formats, or operating-system libraries.

## Assess OpenSlide compatibility

```bash
wsi-converter check /data/wsis/slide.vsi
wsi-converter check /data/wsis/ --recursive --output compatibility/
wsi-converter check /data/wsis/slide.tif --require-pyramid --require-mpp
```

The checker opens the image, captures format and pyramid metadata, and reads small regions at the upper left, centre, lower right, and a representative pyramid level when available. Requirements can be configured: opening, dimensions, pyramid, representative reads, MPP, and associated images. The default requires a successful open, usable dimensions, and representative reads; MPP absence is reported as a warning. Use `--require-mpp` or `--require-pyramid` when your analysis needs those capabilities.

Batch `check` returns exit code 0 when assessment completes, including when some files are incompatible or unreadable. A checker or command failure returns non-zero. Reports include the per-file result and summary counts. `--output` writes `compatibility.json` and `compatibility.csv`; `--json [PATH]` and `--csv [PATH]` can write either report directly (omit the path to print to stdout).

## Inspect series

Inspection uses Bio-Formats' logical, unflattened series inventory and OME-XML names. It reports dimensions, pyramid resolution counts, classification evidence, and associated images:

```bash
wsi-converter inspect /data/wsis/Image_M-126_H\&E.vsi
wsi-converter inspect /data/wsis/Image_M-126_H\&E.vsi --json series.json
```

You can provide an explicit Bio-Formats installation directory when it is not on `PATH`:

```bash
uv run wsi-converter inspect /data/wsis/sample.vsi --bioformats-home /opt/bioformats
```

Series IDs are specific to a source file. `label`, `overview`, `macro`, and thumbnail images are reported as associated images. Pyramid levels remain grouped with their logical image rather than appearing as selectable images. Unknown images remain visible in inspection reports and are not silently converted.

## Convert and verify

```bash
wsi-converter inspect /data/wsis/Image_M-126_H\&E.vsi
wsi-converter convert /data/wsis/Image_M-126_H\&E.vsi \
  --output /data/converted/
```

Without a selector, conversion processes every confidently identified primary WSI image in the file, each as an independent output. Associated images and unresolved records are reported and excluded. This avoids choosing an arbitrary “first” or “largest” series. To convert one image only, use its exact logical name or ID from `inspect`:

```bash
wsi-converter convert /data/wsis/Image_M-126_H\&E.vsi \
  --series-name 'M-12620x_BF_01H&E' --output /data/converted/
```

`--series` is an advanced numeric override; `--series-name` uses the OME image name exposed by `inspect`. Pyramid-preserving outputs are placed under `<output>/<source-name>/wsi/`. Explicitly flattened exports are placed under `<output>/<source-name>/flattened/`. Names contain the logical image name and source ID. Existing outputs are protected unless `--overwrite` is supplied.

Conversion defaults to `--resolution-mode preserve`, which passes Bio-Formats `-noflat` and requires a matching pyramid from both validators. `--resolution-mode flatten` explicitly exports one flattened resolution; that mode checks matching dimensions and readability without claiming the result is a pyramidal WSI. There is no automatic mode switch. `--fallback-on-validation-failure` opts into trying the other mode after the requested mode fails.

Bio-Formats writes OME-TIFF as BigTIFF with 64-bit offsets so large uncompressed WSI planes remain readable by OpenSlide. This affects the TIFF container structure; pixel compression is not enabled.

Compression is optional and is disabled by default. To request a Bio-Formats codec, pass its method name, for example `--compression LZW`. Supported methods depend on the Bio-Formats version and OME-TIFF writer; Bio-Formats reports an error if the selected method is unavailable. Lossy codecs can change pixel values, so choose a method appropriate to the intended downstream analysis. The selected method (or `uncompressed`) is recorded in the conversion report.

Each conversion attempt is staged and checked independently. Outputs from completed Bio-Formats conversions are retained under `<output>/<source-name>/unverified/` when validation rejects them. These are diagnostic artifacts and are not accepted outputs. Non-empty files left by a failed Bio-Formats process are discarded by default; `--keep-partial-artifacts` retains them in the same directory with a `partial` label. Only outputs passing the checks required by the selected mode go to `wsi/` or `flattened/`.

```bash
wsi-converter convert /data/wsis/ --recursive --only-incompatible \
  --output /data/converted/ --dry-run
```

`--only-incompatible` bases its decision on actual OpenSlide assessment. A dry run reports every planned logical image and output path without converting. Recursive conversion isolates failures by image and continues with remaining images and files. Use `--json PATH` to write a live, atomically updated hierarchical report: run → source → logical WSI → attempt → independent checks and artifact disposition. `passed` means every eligible image passed its selected mode's required checks; `partial` means at least one attempt or image failed while work completed; `failed` means no conversion completed successfully. `planned` and `skipped` distinguish dry runs and runs with no conversion work. The report includes per-source and per-image counts. Bare `--json` prints the final JSON to stdout; progress remains visible in the terminal.

The conversion JSON is intentionally compact: its source inventory includes series identity, role, dimensions, resolution count, and calibration where available, but omits raw vendor metadata and full process output. Use `inspect --json PATH` when you need the detailed Bio-Formats inventory and metadata.

Run a standalone verification:

```bash
wsi-converter verify /data/converted/slide.ome.tiff --require-pyramid
```

During conversion, an output is reported as verified only if Bio-Formats confirms matching dimensions and pyramid structure and OpenSlide opens it with required pyramid and pixel-read checks passing. The standalone `verify` command runs OpenSlide checks; it does not repeat Bio-Formats validation. An OME-TIFF extension alone does not guarantee OpenSlide compatibility: OpenSlide documents generic tiled TIFF support when the initial image is tiled and lower-resolution tiled images are marked as reduced-resolution levels. It does not list OME-TIFF as a separately guaranteed format. [OpenSlide supported formats](https://openslide.org/formats/) and [generic tiled TIFF requirements](https://openslide.org/formats/generic-tiff/).

## Olympus VSI files

Some `.vsi` files use a neighbouring companion directory such as `_Image_M-126_H&E_/`. The checker reports whether this expected directory exists. A missing companion is a warning, not a definitive invalid-file judgement; OpenSlide and Bio-Formats are still allowed to inspect the actual input. Source images and companion directories are never renamed, moved, rewritten, or deleted.

## Reports and reproducibility

JSON captures structured checks, metadata, warnings, errors, series selection, conversion parameters, tool version where available, timestamps, output path, and verification result. CSV is a compact per-file summary for spreadsheet and batch workflows. Reports avoid extracting unnecessary clinical metadata.

## Troubleshooting

* **OpenSlide binding or native library missing:** run `uv sync --locked` to install the declared `openslide-python` and `openslide-bin` packages. If no compatible `openslide-bin` wheel exists for your platform, install a system OpenSlide library and check the environment again with `wsi-converter doctor`.
* **Bio-Formats executable missing:** install the Bio-Formats command-line tools, add them to `PATH`, set `BIOFORMATS_HOME`, or pass the executable options.
* **VSI companion data not found:** check that the supplied `.vsi` and its companion directory are together; inspection may still succeed without the expected directory.
* **Corrupt/unreadable image:** inspect the recorded OpenSlide errors and try Bio-Formats inspection where appropriate.
* **Unknown image role:** inspect the OME image name, dimensions, resolution count, and classification evidence. Unknown records are reported and excluded from automatic conversion; use `--series` or `--series-name` only when you have identified the intended logical image.
* **Output exists:** choose another output directory or explicitly pass `--overwrite`.
* **Verification fails:** the converted file remains unverified; check the output assessment in JSON and the Bio-Formats diagnostics, then review conversion parameters and tool versions.

## Environment diagnostics

Run the read-only dependency check inside the uv environment:

```bash
uv run wsi-converter doctor
uv run wsi-converter doctor --bioformats-home /opt/bioformats
```

It reports Python and package versions/locations, distinguishes the `openslide-python` binding from the native OpenSlide library, checks `bfconvert` and `showinf`, and probes Java and Bio-Formats versions where possible. It reports missing components and does not install, update, or configure software.

## Docker and deployment

The Docker image is defined by `Dockerfile`. It uses the locked uv environment (including `openslide-bin`), OpenJDK 17 headless, and the pinned Bio-Formats 8.5.0 command-line bundle. The build verifies the official Bio-Formats checksums, sets `BIOFORMATS_HOME=/opt/bioformats`, and runs `wsi-converter doctor` before the image can be published. The Bio-Formats source archive, checksum list, build-time dependency report, and resolved OS package versions are retained under `/usr/share/doc/wsi-converter/` in the image.

Build and inspect the image locally:

```bash
docker build -t wsi-converter:local .
docker run --rm wsi-converter:local doctor
```

Use the published GHCR image with mounted input and output directories. Set the container user to your host UID/GID so output files are owned appropriately:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --volume /data/wsis:/input:ro \
  --volume /data/converted:/output \
  ghcr.io/OWNER/REPOSITORY:VERSION \
  convert /input --recursive --output /output --json /output/report.json
```

The image's `BIOFORMATS_HOME` can be overridden to use another Bio-Formats release. Mount the alternate tools directory and use its container path, for example `--volume /opt/other-bftools:/external/bftools:ro --env BIOFORMATS_HOME=/external/bftools`. The CLI `--bioformats-home` option can also select that mounted path.

The `publish-ghcr.yml` workflow publishes on version tags such as `v0.1.0`. It runs Python quality checks, builds the image, runs `doctor` inside that image, and only then pushes it to GHCR. It currently publishes `linux/amd64`; add other architectures after verifying their Python wheels and external runtime combination. Public GHCR visibility is configured in the package settings after the first publish.

## Deployment and external runtime record

For reproducible deployments, pin the container image by digest and record Java, Bio-Formats, and base OS versions separately from `uv.lock`. The lockfile covers the Python environment, including the `openslide-bin` native OpenSlide distribution. For environments using a system-provided OpenSlide library instead, record that native library separately. The package works in headless Docker and Apptainer/Singularity jobs; Bio-Formats can be included in the image or mounted and configured through `BIOFORMATS_HOME`, `--bioformats-home`, `--bfconvert`, or `--showinf`.

Example Apptainer invocation when Bio-Formats is supplied from a mounted directory:

```bash
apptainer exec --bind /opt/bioformats:/opt/bioformats \
  --env BIOFORMATS_HOME=/opt/bioformats image.sif \
  uv run wsi-converter doctor
```

Record at least the Java vendor/version/path, Bio-Formats release and executable paths, OpenSlide native version/library path, and container image digest/base OS release with each deployment. Use the separate [external runtime record](docs/external-runtime.md) for deployment details.

## Tests and local data

Unit tests use synthetic metadata and a fake OpenSlide object. Integration testing requires native OpenSlide, Bio-Formats, and a local representative WSI. Keep proprietary/clinical WSIs outside the repository and provide their paths through local test configuration; no patient slide fixtures are committed.

```bash
uv run pytest
uv run pytest -m integration
uv run ruff check .
uv run mypy .
```

The integration marker is reserved for tests requiring external runtimes or user-supplied WSI data.
