"""Cross-compile Cython extension to multiple platforms using Zig CC."""

import argparse
import base64
import hashlib
import shutil
import subprocess
import tomllib
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ziglang

# ---------------------------------------------------------------------------
# Read project metadata from pyproject.toml (single source of truth)
# ---------------------------------------------------------------------------

_pyproject = tomllib.loads(Path("pyproject.toml").read_text())
PROJECT_NAME = _pyproject["project"]["name"]
VERSION = _pyproject["project"]["version"]
_project_meta = _pyproject["project"]

PYX_SOURCE = Path("src/zigcython/_fast.pyx")
C_SOURCE = Path("src/zigcython/_fast.c")
DIST_DIR = Path("dist")
BUILD_DIR = Path("build")
INCLUDE_DIR = Path("include")

ZIG = Path(ziglang.__path__[0]) / "zig"

# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]

# Platform configs reference directories under include/ that contain
# the platform-specific pyconfig.h and _numpyconfig.h.  To add a new
# platform class, create a new directory with those two files.
PLATFORMS = {
    "manylinux-x86_64": {
        "zig_target": "x86_64-linux-gnu.2.17",
        "ext_suffix_platform": "x86_64-linux-gnu",
        "flags": ["-shared", "-fPIC"],
        "wheel_platform": "manylinux_2_17_x86_64.manylinux2014_x86_64",
        "include": "linux-64bit",
    },
    "manylinux-aarch64": {
        "zig_target": "aarch64-linux-gnu.2.17",
        "ext_suffix_platform": "aarch64-linux-gnu",
        "flags": ["-shared", "-fPIC"],
        "wheel_platform": "manylinux_2_17_aarch64.manylinux2014_aarch64",
        "include": "linux-aarch64",
    },
    "musllinux-x86_64": {
        "zig_target": "x86_64-linux-musl",
        "ext_suffix_platform": "x86_64-linux-musl",
        "flags": ["-shared", "-fPIC"],
        "wheel_platform": "musllinux_1_2_x86_64",
        "include": "linux-64bit",
    },
    "musllinux-aarch64": {
        "zig_target": "aarch64-linux-musl",
        "ext_suffix_platform": "aarch64-linux-musl",
        "flags": ["-shared", "-fPIC"],
        "wheel_platform": "musllinux_1_2_aarch64",
        "include": "linux-aarch64",
    },
    "macos-x86_64": {
        "zig_target": "x86_64-macos",
        "ext_suffix_platform": "darwin",
        "flags": ["-shared", "-fPIC", "-undefined", "dynamic_lookup"],
        "wheel_platform": "macosx_10_13_x86_64",
        "include": "macos-64bit",
    },
    "macos-arm64": {
        "zig_target": "aarch64-macos",
        "ext_suffix_platform": "darwin",
        "flags": ["-shared", "-fPIC", "-undefined", "dynamic_lookup"],
        "wheel_platform": "macosx_11_0_arm64",
        "include": "macos-arm64",
    },
    "windows-x86_64": {
        "zig_target": "x86_64-windows-gnu",
        "ext_suffix_platform": None,  # Windows uses .pyd
        "flags": ["-shared"],
        "wheel_platform": "win_amd64",
        "include": "windows-64bit",
    },
}

# Patch versions with Windows embeddable zips on python.org.
# Older series (3.10, 3.11) stopped getting Windows binaries after their
# last bugfix release, so we pin to the last version that shipped them.
WINDOWS_EMBED_VERSIONS = {
    "3.10": "3.10.11",
    "3.11": "3.11.9",
    "3.12": "3.12.10",
    "3.13": "3.13.12",
    "3.14": "3.14.3",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ext_suffix(py_version: str, platform: dict) -> str:
    minor = py_version.split(".")[1]
    if platform["ext_suffix_platform"] is None:
        return ".pyd"
    return f".cpython-3{minor}-{platform['ext_suffix_platform']}.so"


def wheel_tag(py_version: str, platform: dict) -> str:
    minor = py_version.split(".")[1]
    return f"cp3{minor}-cp3{minor}-{platform['wheel_platform']}"


# ---------------------------------------------------------------------------
# Windows DLL fetching
# ---------------------------------------------------------------------------


def fetch_windows_dll(py_version: str) -> Path:
    """Download the embeddable Python zip and extract python3XX.dll."""
    minor = py_version.split(".")[1]
    dll_name = f"python3{minor}.dll"
    cache_dir = BUILD_DIR / "windows-python" / py_version
    dll_path = cache_dir / dll_name

    if dll_path.exists():
        return dll_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    full_ver = WINDOWS_EMBED_VERSIONS[py_version]
    url = f"https://www.python.org/ftp/python/{full_ver}/python-{full_ver}-embed-amd64.zip"
    zip_path = cache_dir / "python-embed.zip"

    print(f"[windows] Downloading embeddable Python {full_ver} ...")
    urllib.request.urlretrieve(url, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.lower() == dll_name.lower():
                zf.extract(name, cache_dir)
                print(f"[windows] Extracted {name}")
                break

    zip_path.unlink()
    return dll_path


# ---------------------------------------------------------------------------
# Python version include paths
# ---------------------------------------------------------------------------

# Cache of (python_include, numpy_include) per Python version.
_include_cache: dict[str, tuple[Path, Path]] = {}


def get_includes(py_version: str) -> tuple[Path, Path]:
    """Install the target Python version via uv and return its include paths."""
    if py_version in _include_cache:
        return _include_cache[py_version]

    venv_dir = BUILD_DIR / "venvs" / py_version
    if not venv_dir.exists():
        print(f"[setup] Creating venv for Python {py_version} ...")
        subprocess.run(
            ["uv", "venv", "--python", py_version, str(venv_dir)],
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_dir), "numpy"],
            check=True,
        )

    # Query include paths from the installed Python.
    python = venv_dir / "bin" / "python"
    out = subprocess.run(
        [str(python), "-c",
         "import sysconfig, numpy; "
         "print(sysconfig.get_path('include')); "
         "print(numpy.get_include())"],
        capture_output=True, text=True, check=True,
    )
    py_inc, np_inc = [Path(p) for p in out.stdout.strip().splitlines()]
    _include_cache[py_version] = (py_inc, np_inc)
    return py_inc, np_inc


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


def cythonize():
    """Run Cython to produce C source."""
    print(f"[cythonize] {PYX_SOURCE} -> {C_SOURCE}")
    subprocess.run(
        ["cython", str(PYX_SOURCE), "-o", str(C_SOURCE)],
        check=True,
    )


def compile_target(py_version: str, plat_name: str, plat: dict) -> Path:
    """Compile C source for one (python_version, platform) combination."""
    suffix = ext_suffix(py_version, plat)
    build_dir = BUILD_DIR / py_version / plat_name
    build_dir.mkdir(parents=True, exist_ok=True)
    out_path = build_dir / f"_fast{suffix}"

    python_include, numpy_include = get_includes(py_version)

    # Merge version-correct Python/numpy headers with platform-specific overrides.
    inc_dir = build_dir / "include"
    py_inc = inc_dir / "python"
    np_inc = inc_dir / "numpy"
    if inc_dir.exists():
        shutil.rmtree(inc_dir)

    override_dir = INCLUDE_DIR / plat["include"]
    shutil.copytree(python_include, py_inc)
    shutil.copy2(override_dir / "pyconfig.h", py_inc / "pyconfig.h")
    shutil.copytree(numpy_include, np_inc)
    shutil.copy2(override_dir / "_numpyconfig.h", np_inc / "numpy" / "_numpyconfig.h")

    extra_args = []
    if plat_name == "windows-x86_64":
        dll_path = fetch_windows_dll(py_version)
        extra_args.append(str(dll_path))

    cmd = [
        str(ZIG), "cc",
        "-target", plat["zig_target"],
        *plat["flags"],
        "-DNDEBUG",
        f"-I{py_inc}", f"-I{np_inc}",
        "-o", str(out_path),
        str(C_SOURCE),
        *extra_args,
    ]

    print(f"[compile] {py_version}/{plat_name}: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return out_path


def _expand_tags(compressed_tag: str) -> list[str]:
    """Expand a compressed wheel tag into individual tags.

    Maturin-style: dots within a component represent multiple values.
    e.g. "cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64"
    expands to two tags with different platform parts.
    """
    parts = compressed_tag.split("-")
    if len(parts) != 3:
        return [compressed_tag]
    python, abi, platform = parts
    # Each component can have dot-separated alternatives
    pythons = python.split(".")
    abis = abi.split(".")
    platforms = platform.split(".")
    return [
        f"{py}-{ab}-{pl}"
        for py in pythons
        for ab in abis
        for pl in platforms
    ]


def _record_line(path: str, data: bytes) -> str:
    """Build a RECORD entry: path,sha256=<urlsafe-b64>,<length>."""
    digest = hashlib.sha256(data).digest()
    hash_str = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{path},sha256={hash_str},{len(data)}"


def build_wheel(py_version: str, plat: dict, ext_path: Path) -> Path:
    """Build a wheel by writing the zip archive directly (maturin-style).

    Instead of staging files on disk and shelling out to `wheel pack`,
    we write the zip ourselves — giving us control over ordering (dist-info
    last, per PEP 427), RECORD hashes (SHA-256, base64url), and metadata
    version (2.4).
    """
    tag = wheel_tag(py_version, plat)
    suffix = ext_suffix(py_version, plat)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    dist_info_prefix = f"{PROJECT_NAME}-{VERSION}.dist-info"

    # -- Collect package files (added to zip before dist-info) ---------------
    pkg_files: list[tuple[str, bytes]] = []

    init_data = Path("src/zigcython/__init__.py").read_bytes()
    pkg_files.append((f"{PROJECT_NAME}/__init__.py", init_data))

    ext_data = ext_path.read_bytes()
    pkg_files.append((f"{PROJECT_NAME}/_fast{suffix}", ext_data))

    # -- Build dist-info contents -------------------------------------------

    # METADATA (PEP 566 / Metadata 2.4)
    metadata_lines = [
        "Metadata-Version: 2.4",
        f"Name: {PROJECT_NAME}",
        f"Version: {VERSION}",
    ]
    if "description" in _project_meta:
        metadata_lines.append(f"Summary: {_project_meta['description']}")
    if "requires-python" in _project_meta:
        metadata_lines.append(
            f"Requires-Python: {_project_meta['requires-python']}"
        )
    for dep in _project_meta.get("dependencies", []):
        metadata_lines.append(f"Requires-Dist: {dep}")
    metadata_bytes = ("\n".join(metadata_lines) + "\n").encode()

    # WHEEL — expand compressed tags into individual Tag lines (maturin-style)
    wheel_lines = [
        "Wheel-Version: 1.0",
        "Generator: zig-cython build.py",
        "Root-Is-Purelib: false",
    ]
    for expanded in _expand_tags(tag):
        wheel_lines.append(f"Tag: {expanded}")
    wheel_bytes = ("\n".join(wheel_lines) + "\n").encode()

    top_level_bytes = f"{PROJECT_NAME}\n".encode()

    dist_info_files: list[tuple[str, bytes]] = [
        (f"{dist_info_prefix}/METADATA", metadata_bytes),
        (f"{dist_info_prefix}/WHEEL", wheel_bytes),
        (f"{dist_info_prefix}/top_level.txt", top_level_bytes),
    ]

    # -- Build RECORD -------------------------------------------------------
    record_entries = []
    for path, data in pkg_files + dist_info_files:
        record_entries.append(_record_line(path, data))
    # RECORD itself gets an empty hash entry
    record_path = f"{dist_info_prefix}/RECORD"
    record_entries.append(f"{record_path},,")
    record_bytes = ("\n".join(record_entries) + "\n").encode()

    # -- Write zip (package files first, dist-info last per PEP 427) --------
    wheel_path = DIST_DIR / f"{PROJECT_NAME}-{VERSION}-{tag}.whl"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in pkg_files:
            zf.writestr(path, data)
        for path, data in dist_info_files:
            zf.writestr(path, data)
        zf.writestr(record_path, record_bytes)

    return wheel_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", nargs="*", help="Python versions (e.g. 3.13)")
    parser.add_argument("--platform", nargs="*", help="Platforms (e.g. manylinux-x86_64)")
    args = parser.parse_args()

    versions = args.python or PYTHON_VERSIONS
    platforms = {k: PLATFORMS[k] for k in (args.platform or PLATFORMS)}

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    cythonize()

    # Pre-fetch Windows DLLs in parallel.
    if "windows-x86_64" in platforms:
        with ThreadPoolExecutor(max_workers=len(versions)) as pool:
            futures = [pool.submit(fetch_windows_dll, v) for v in versions]
            for f in futures:
                f.result()  # raise on failure

    wheels = []
    for py_version in versions:
        for plat_name, plat in platforms.items():
            ext_path = compile_target(py_version, plat_name, plat)
            whl = build_wheel(py_version, plat, ext_path)
            wheels.append(whl)

    print(f"\nBuilt {len(wheels)} wheel(s):")
    for w in wheels:
        print(f"  {w}")


if __name__ == "__main__":
    main()
