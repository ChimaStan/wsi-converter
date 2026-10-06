# Contributing

Thanks for your interest in contributing to `wsi_converter`. This guide covers the local development workflow, tests, and container maintenance. User installation and CLI instructions are in the [README](README.md).

## Development setup

Use `uv` for the project Python environment and dependencies. From the repository root:

```bash
uv python install
uv sync --locked --group dev
```

The project uses the Python version declared by `.python-version`; `uv.lock` pins the Python dependencies, including `openslide-python` and `openslide-bin`. The latter supplies the OpenSlide native library in the standard environment. Java and Bio-Formats are external runtimes and are not installed or managed by uv. If a deployment substitutes an OS-provided OpenSlide library for `openslide-bin`, that system dependency is also external to uv. See the [external runtime record](docs/external-runtime.md) for deployment versions and locations.

After changing dependency declarations in `pyproject.toml`, run `uv lock` and commit the updated `uv.lock`. Use `uv sync --locked` to ensure the environment matches the committed lockfile.

## Quality checks

Before opening a pull request, run:

```bash
uv run ruff check .
uv run mypy .
uv run pytest
```

The regular unit tests use synthetic metadata and fakes; they do not require a real WSI or Bio-Formats installation. GitHub Actions runs these same quality checks for pushes and pull requests.

## Integration tests

Integration tests are opt-in and require a local representative VSI, OpenSlide, Java, and Bio-Formats. Keep proprietary or clinical WSI data outside the repository. Set `WSI_CONVERTER_TEST_VSI` to the VSI file and configure Bio-Formats with `BIOFORMATS_HOME` or the usual CLI environment configuration:

```bash
WSI_CONVERTER_TEST_VSI=path/to/wsis/sample.vsi \
BIOFORMATS_HOME=path/to/bioformats \
uv run pytest -m integration
```

If `WSI_CONVERTER_TEST_SERIES` is omitted, the integration test inspects the file and checks the automatic WSI selection plan without converting. To exercise a conversion, set it to a numeric Bio-Formats series ID reported by `inspect`:

```bash
WSI_CONVERTER_TEST_VSI=path/to/wsis/sample.vsi \
WSI_CONVERTER_TEST_SERIES=SERIES_ID \
BIOFORMATS_HOME=path/to/bioformats \
uv run pytest -m integration
```

Replace `SERIES_ID` with the numeric series ID reported by `inspect` for the test image. The selected conversion must pass the test's Bio-Formats and OpenSlide validation checks. The test writes temporary output under pytest's temporary directory.

## Container maintenance and release

Build and smoke-check the runtime image locally:

```bash
docker build -t wsi-converter:local .
docker run --rm wsi-converter:local doctor
```

The Dockerfile pins the Python base image, installs Java, and downloads a versioned Bio-Formats bundle with checksum verification. The Python packages, including `openslide-bin`, come from `uv.lock`. The image build runs `wsi-converter doctor` and fails if its runtime dependencies are unavailable.

The `publish-ghcr.yml` workflow publishes only when a tag beginning with `v` is pushed. After release changes are merged to `main`, create and push a tag matching the version in `pyproject.toml`, for example:

```bash
git switch main
git pull --ff-only origin main
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

The release workflow reruns Ruff, mypy, and pytest, builds a `linux/amd64` image, runs `wsi-converter doctor` inside it, then publishes to GHCR if those checks pass. It creates semantic-version and commit-SHA image tags and includes provenance and an SBOM. Pull requests and ordinary branch pushes run CI but do not publish the image.

## Pull requests

Use a focused branch and pull request for changes. Describe the behavior changed and the checks you ran. Avoid adding proprietary or clinical slide data, generated conversion outputs, or local environment files to commits.
