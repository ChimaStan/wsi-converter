# syntax=docker/dockerfile:1.7

# Pinned multi-platform Python 3.11 Bookworm image. Update this digest as a
# reviewed maintenance change when applying base-image security updates.
ARG PYTHON_BASE_IMAGE=python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb

FROM ${PYTHON_BASE_IMAGE} AS bioformats
ARG BIOFORMATS_VERSION=8.5.0
ENV BIOFORMATS_HOME=/opt/bioformats

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Fetch a versioned OME release, verify both the tool bundle and corresponding
# source archive against OME's published checksums, and retain the source and
# checksum list for redistribution notices and reproducibility. The tools
# archive extracts into a top-level bftools/ directory.
RUN set -eux; \
    download_dir="$(mktemp -d)"; \
    release_url="https://downloads.openmicroscopy.org/bio-formats/${BIOFORMATS_VERSION}/artifacts"; \
    curl --fail --location --silent --show-error \
        "${release_url}/bftools.zip" -o "${download_dir}/bftools.zip"; \
    curl --fail --location --silent --show-error \
        "${release_url}/bioformats-${BIOFORMATS_VERSION}.zip" \
        -o "${download_dir}/bioformats-${BIOFORMATS_VERSION}.zip"; \
    curl --fail --location --silent --show-error \
        "${release_url}/SHASUMS" -o "${download_dir}/SHASUMS"; \
    grep -F 'bftools.zip' "${download_dir}/SHASUMS" \
        > "${download_dir}/selected-shasums"; \
    grep -F "bioformats-${BIOFORMATS_VERSION}.zip" \
        "${download_dir}/SHASUMS" >> "${download_dir}/selected-shasums"; \
    test "$(wc -l < "${download_dir}/selected-shasums")" -eq 2; \
    (cd "${download_dir}" && sha256sum --check selected-shasums); \
    mkdir -p "${BIOFORMATS_HOME}" /usr/share/doc/wsi-converter; \
    unzip -q "${download_dir}/bftools.zip" -d "${BIOFORMATS_HOME}"; \
    test -f "${BIOFORMATS_HOME}/bftools/bfconvert"; \
    test -f "${BIOFORMATS_HOME}/bftools/showinf"; \
    chmod 0755 "${BIOFORMATS_HOME}/bftools/bfconvert" "${BIOFORMATS_HOME}/bftools/showinf"; \
    install -m 0644 "${download_dir}/bioformats-${BIOFORMATS_VERSION}.zip" \
        "/usr/share/doc/wsi-converter/bioformats-${BIOFORMATS_VERSION}-source.zip"; \
    install -m 0644 "${download_dir}/SHASUMS" \
        /usr/share/doc/wsi-converter/bioformats-SHASUMS; \
    rm -rf "${download_dir}"

FROM ghcr.io/astral-sh/uv:0.12.22 AS uv

FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG BIOFORMATS_VERSION=8.5.0

ENV BIOFORMATS_HOME=/opt/bioformats/bftools \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv is pinned to a release tag. uv.lock independently pins the Python
# application dependencies
COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY --from=bioformats /opt/bioformats /opt/bioformats
COPY --from=bioformats /usr/share/doc/wsi-converter/ /usr/share/doc/wsi-converter/

WORKDIR /app

# Copy only the metadata and source required to install the application. The
# .dockerignore excludes local environments, Bio-Formats copies, and WSI data.
COPY .python-version pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable

ENV PATH="/opt/venv/bin:${PATH}"

# Fail the image build if any runtime is unavailable and preserve its resolved
# versions and paths in the image's deployment record.
RUN set -eux; \
    wsi-converter doctor --json /usr/share/doc/wsi-converter/doctor.json; \
    dpkg-query -W openjdk-17-jre-headless libc6 \
        > /usr/share/doc/wsi-converter/os-packages.txt; \
    printf 'Bio-Formats %s\n' "${BIOFORMATS_VERSION}" \
        > /usr/share/doc/wsi-converter/bioformats-version.txt

RUN groupadd --gid 10001 wsi-converter \
    && useradd --uid 10001 --gid 10001 --create-home \
        --home-dir /home/wsi-converter --shell /usr/sbin/nologin wsi-converter \
    && mkdir -p /work \
    && chown 10001:10001 /work

WORKDIR /work
USER 10001:10001

ENTRYPOINT ["wsi-converter"]
CMD ["--help"]
