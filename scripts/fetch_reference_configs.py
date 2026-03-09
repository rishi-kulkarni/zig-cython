#!/usr/bin/env python3
"""Fetch reference pyconfig.h and _numpyconfig.h for each target platform.

Downloads prebuilt CPython from python-build-standalone and numpy wheels
from PyPI, then extracts the platform-specific config headers into include/.

Usage:
    python scripts/fetch_reference_configs.py          # update all platforms
    python scripts/fetch_reference_configs.py linux-64bit  # update one platform
"""

from __future__ import annotations

import io
import json
import re
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration — edit these to update versions or add platforms
# ---------------------------------------------------------------------------

# python-build-standalone release tag (date-based, from
# https://github.com/indygreg/python-build-standalone/releases)
PBS_RELEASE = "20260303"

# CPython version shipped in that release
PBS_CPYTHON = "3.13.12"

# NumPy version to pull _numpyconfig.h from
NUMPY_VERSION = "2.4.3"

# Map each include/<dir> to its upstream sources.
#
#   pbs_triple:      python-build-standalone target triple
#   numpy_platform:  regex matched against numpy wheel filenames on PyPI
#
# To add a new platform: create a new entry here and a matching directory
# will be populated under include/.
MATRIX = {
    "linux-64bit": {
        "pbs_triple": "x86_64-unknown-linux-gnu",
        "numpy_platform": r"manylinux_.*x86_64",
    },
    "macos-64bit": {
        "pbs_triple": "x86_64-apple-darwin",
        "numpy_platform": r"macosx_10_13_x86_64",
    },
    "windows-64bit": {
        "pbs_triple": "x86_64-pc-windows-msvc",
        "numpy_platform": r"win_amd64",
    },
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
INCLUDE_DIR = ROOT / "include"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _download(url: str) -> bytes:
    """Download a URL and return the raw bytes."""
    print(f"  downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "zig-cython/fetch"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def _pbs_url(triple: str) -> str:
    """Build the python-build-standalone download URL for a given triple."""
    return (
        f"https://github.com/indygreg/python-build-standalone/releases/download/"
        f"{PBS_RELEASE}/cpython-{PBS_CPYTHON}+{PBS_RELEASE}-{triple}"
        f"-install_only_stripped.tar.gz"
    )


def _pyconfig_candidates_in_tar() -> list[str]:
    """Return candidate suffixes for pyconfig.h inside a PBS tarball."""
    minor = PBS_CPYTHON.split(".")[1]
    return [
        f"python/include/python3.{minor}/pyconfig.h",  # Unix layout
        "python/include/pyconfig.h",                     # Windows layout
    ]


def _numpy_wheel_url(platform_re: str) -> str:
    """Query PyPI JSON API and return the wheel URL matching *platform_re*."""
    api_url = f"https://pypi.org/pypi/numpy/{NUMPY_VERSION}/json"
    data = json.loads(_download(api_url))
    pat = re.compile(platform_re)
    minor = PBS_CPYTHON.split(".")[1]
    # Match standard (non-free-threaded) cpython wheels: cp3XX-cp3XX-<platform>
    tag_prefix = f"cp3{minor}-cp3{minor}-"
    for entry in data["urls"]:
        fn = entry["filename"]
        if fn.endswith(".whl") and tag_prefix in fn and pat.search(fn):
            return entry["url"]
    raise RuntimeError(
        f"No numpy {NUMPY_VERSION} wheel found matching platform "
        f"/{platform_re}/ for {tag_prefix}"
    )


def _numpyconfig_path_in_wheel() -> list[str]:
    """Return candidate paths for _numpyconfig.h inside a numpy wheel."""
    return [
        "numpy/_core/include/numpy/_numpyconfig.h",  # numpy 2.x
        "numpy/core/include/numpy/_numpyconfig.h",    # numpy 1.x
    ]


# ---------------------------------------------------------------------------
# Per-platform extraction
# ---------------------------------------------------------------------------


def fetch_pyconfig(triple: str, dest: Path) -> None:
    """Download a PBS tarball and extract pyconfig.h to *dest*."""
    url = _pbs_url(triple)
    data = _download(url)
    candidates = _pyconfig_candidates_in_tar()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for candidate in candidates:
            for member in tf.getmembers():
                if member.name == candidate or member.name.endswith(candidate):
                    f = tf.extractfile(member)
                    if f is None:
                        raise RuntimeError(
                            f"Could not read {member.name} from tarball"
                        )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(f.read())
                    print(f"  extracted {member.name} -> {dest}")
                    return
    raise RuntimeError(
        f"pyconfig.h not found in {url} (tried {candidates})"
    )


def fetch_numpyconfig(platform_re: str, dest: Path) -> None:
    """Download a numpy wheel and extract _numpyconfig.h to *dest*."""
    url = _numpy_wheel_url(platform_re)
    data = _download(url)
    candidates = _numpyconfig_path_in_wheel()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for candidate in candidates:
            if candidate in zf.namelist():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(candidate))
                print(f"  extracted {candidate} -> {dest}")
                return
    raise RuntimeError(
        f"_numpyconfig.h not found in {url} "
        f"(tried {candidates})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def update_platform(name: str, cfg: dict) -> None:
    """Fetch both config headers for a single platform."""
    print(f"\n[{name}]")
    out_dir = INCLUDE_DIR / name
    fetch_pyconfig(cfg["pbs_triple"], out_dir / "pyconfig.h")
    fetch_numpyconfig(cfg["numpy_platform"], out_dir / "_numpyconfig.h")


def main() -> None:
    targets = sys.argv[1:] or list(MATRIX)
    for name in targets:
        if name not in MATRIX:
            sys.exit(f"Unknown platform: {name!r}  (known: {list(MATRIX)})")
        update_platform(name, MATRIX[name])
    print("\nDone.")


if __name__ == "__main__":
    main()
