# External runtime record

This record tracks deployment components whose versions are not pinned by `uv.lock`—Java, Bio-Formats, and the container operating system—and records where the OpenSlide native library comes from. In the default environment, `openslide-bin` is a Python dependency pinned by `uv.lock`, including hashes for its platform wheels; its bundled native OpenSlide library is therefore installed through the locked Python environment. Keep this record with each deployment image or HPC environment definition. The Dockerfile in this repository currently defines OpenJDK 17, Bio-Formats 8.5.0, and a digest-pinned Python 3.11 slim Bookworm base image; record the exact resolved Java package and published container digest for each release.

| Component | Version/release | Location | Source or image digest |
| --- | --- | --- | --- |
| Java runtime | OpenJDK 17 headless (record exact build version) | `/usr/bin/java` | Dockerfile + published image digest |
| Bio-Formats | 8.5.0 | `/opt/bioformats` | OME `bftools.zip`; checksum verified from versioned `SHASUMS` |
| `bfconvert` | 8.5.0 | `/opt/bioformats/bftools/bfconvert` | Included in `bftools.zip` |
| `showinf` | 8.5.0 | `/opt/bioformats/bftools/showinf` | Included in `bftools.zip` |
| OpenSlide native library | Supplied by locked `openslide-bin` 4.0.1.2 (native library reports 4.0.1) | Python environment | `uv.lock` package version and wheel hashes |
| Operating system/container base | Python 3.11 slim Bookworm | Container image | Digest pinned in `Dockerfile`; record published image digest |

`openslide-python` and `openslide-bin` remain separate dependencies: the former is the Python binding and the latter provides native OpenSlide binaries. Both are currently uv-managed Python dependencies in this project and are pinned by `uv.lock`. The Docker build runs `wsi-converter doctor` to check that both the binding and native library load. For deployments that replace `openslide-bin` with a system library, record that library's version and location here instead.

The application only checks and uses Java and Bio-Formats; it does not download, install, update, or configure them. The Dockerfile installs these runtimes into the image and configures `BIOFORMATS_HOME=/opt/bioformats/bftools`, matching the directory layout inside the official `bftools.zip` archive. Users can override that default with `BIOFORMATS_HOME`, `--bioformats-home`, `--bfconvert`, or `--showinf`. Java is discovered through `PATH` or `JAVA_HOME`. The OpenSlide library is loaded from the locked `openslide-bin` distribution unless the deployment deliberately supplies a system alternative. The image stores the successful build-time `doctor` result at `/usr/share/doc/wsi-converter/doctor.json`, installed OS package versions in `os-packages.txt`, and the Bio-Formats release in `bioformats-version.txt`.

For Docker or Apptainer/Singularity, record versions in this table and pin the image digest at deployment time. The same CLI and external path options work in non-interactive container and HPC jobs. `uv.lock` pins the Python packages, including `openslide-bin` and its bundled native OpenSlide distribution; it does not pin Java, Bio-Formats, the operating-system base image, or OS packages installed separately from Python dependencies. If `openslide-bin` is replaced with an OS-provided OpenSlide library, record that library's version and path here because it is not covered by `uv.lock`.
