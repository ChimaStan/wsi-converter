# Container runtime inventory

This maintainer-facing inventory describes what the Dockerfile places in the container and how those components' versions are controlled or recorded. It is not a user setup guide, and it does not replace recording the immutable image digest for a specific deployment. User instructions for pulling and running the image are in the [README](../README.md); contributor setup and release steps are in [CONTRIBUTING.md](../CONTRIBUTING.md).

| Component | Current container configuration | Version source and record |
| --- | --- | --- |
| Java | OpenJDK 17 headless; `/usr/bin/java` | The Dockerfile installs `openjdk-17-jre-headless` from Debian Bookworm. The resolved Debian package version is written to `/usr/share/doc/wsi-converter/os-packages.txt`; `doctor.json` records the runtime version. The exact package revision is resolved at image build time rather than pinned in `uv.lock`. |
| Bio-Formats | 8.5.0; `BIOFORMATS_HOME=/opt/bioformats/bftools` | Versioned OME `bftools.zip`; selected files are verified against OME's `SHASUMS`. The release is written to `/usr/share/doc/wsi-converter/bioformats-version.txt`. |
| `bfconvert` | `/opt/bioformats/bftools/bfconvert` | Included in the versioned `bftools.zip`; its reported version is checked by `doctor`. |
| `showinf` | `/opt/bioformats/bftools/showinf` | Included in the versioned `bftools.zip`; its reported version is checked by `doctor`. |
| OpenSlide native library | Provided by `openslide-bin` 4.0.1.2; native library reports 4.0.1 | `openslide-bin` is a Python dependency pinned in `uv.lock`, including platform wheel hashes. The build-time `doctor.json` records the loaded native library path and version. |
| Container base | Python 3.11 slim Bookworm; `linux/amd64` image | The base image digest is pinned in the Dockerfile. The release workflow builds for `linux/amd64`; use the published image digest when a deployment needs an immutable image reference. |

The Python binding `openslide-python` and native distribution `openslide-bin` are separate dependencies, both managed by uv and pinned in `uv.lock`. The Docker image gets OpenSlide from the locked `openslide-bin` wheel. If a deployment replaces it with an OS-provided OpenSlide library, record that library's version and location in the deployment's own environment record; that system library is outside `uv.lock`.

The application diagnoses and uses Java and Bio-Formats but does not download, install, or update them. The Dockerfile supplies them in the image. `BIOFORMATS_HOME`, `--bioformats-home`, `--bfconvert`, and `--showinf` can point to an alternate Bio-Formats installation when needed. The build stores the `doctor` result, selected OS package versions, and Bio-Formats release under `/usr/share/doc/wsi-converter/`. The publishing workflow attaches provenance and an SBOM to the released image.

For each production or HPC deployment, record the exact image digest used by the job. For a locally built image, preserve the generated runtime metadata with the deployment artifacts; for a published image, use the digest reported by GHCR rather than relying only on the mutable version tag.
