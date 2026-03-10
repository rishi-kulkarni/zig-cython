"""Cross-compile Cython extension to multiple platforms using Zig CC."""

import argparse
import hashlib
import base64
import csv
import io
import shutil
import subprocess
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ziglang

PROJECT_NAME = "zigcython"
VERSION = "0.1.0"
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


def record_hash(path: str, data: bytes) -> tuple[str, str, str]:
    digest = hashlib.sha256(data).digest()
    hash_str = "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return (path, hash_str, str(len(data)))


def build_wheel(py_version: str, plat: dict, ext_path: Path) -> Path:
    tag = wheel_tag(py_version, plat)
    suffix = ext_suffix(py_version, plat)
    wheel_name = f"{PROJECT_NAME}-{VERSION}-{tag}.whl"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    wheel_path = DIST_DIR / wheel_name

    dist_info = f"{PROJECT_NAME}-{VERSION}.dist-info"
    records = []

    init_py = Path("src/zigcython/__init__.py").read_bytes()
    ext_data = ext_path.read_bytes()

    metadata_content = (
        f"Metadata-Version: 2.1\n"
        f"Name: {PROJECT_NAME}\n"
        f"Version: {VERSION}\n"
        f"Summary: Cython extension cross-compiled with Zig\n"
        f"Requires-Python: >=3.10\n"
        f"Requires-Dist: numpy>=2.0\n"
    ).encode()

    wheel_content = (
        f"Wheel-Version: 1.0\n"
        f"Generator: build.py\n"
        f"Root-Is-Purelib: false\n"
        f"Tag: {tag}\n"
    ).encode()

    top_level = b"zigcython\n"

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        pkg_init = "zigcython/__init__.py"
        whl.writestr(pkg_init, init_py)
        records.append(record_hash(pkg_init, init_py))

        ext_name = f"zigcython/_fast{suffix}"
        whl.writestr(ext_name, ext_data)
        records.append(record_hash(ext_name, ext_data))

        meta_path = f"{dist_info}/METADATA"
        whl.writestr(meta_path, metadata_content)
        records.append(record_hash(meta_path, metadata_content))

        wheel_path_in_zip = f"{dist_info}/WHEEL"
        whl.writestr(wheel_path_in_zip, wheel_content)
        records.append(record_hash(wheel_path_in_zip, wheel_content))

        top_level_path = f"{dist_info}/top_level.txt"
        whl.writestr(top_level_path, top_level)
        records.append(record_hash(top_level_path, top_level))

        record_path = f"{dist_info}/RECORD"
        records.append((record_path, "", ""))
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in records:
            writer.writerow(row)
        whl.writestr(record_path, buf.getvalue())

    print(f"[wheel] {wheel_path}")
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
