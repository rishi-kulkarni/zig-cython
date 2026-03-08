"""Cross-compile Cython extension to multiple platforms using Zig CC."""

import hashlib
import base64
import csv
import io
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

PROJECT_NAME = "zigcython"
VERSION = "0.1.0"
PYX_SOURCE = Path("src/zigcython/_fast.pyx")
C_SOURCE = Path("src/zigcython/_fast.c")
DIST_DIR = Path("dist")
INCLUDE_DIR = Path("include")

WINDOWS_PYTHON_URL = (
    "https://www.python.org/ftp/python/3.13.1/python-3.13.1-embed-amd64.zip"
)
WINDOWS_PYTHON_DIR = Path("build/windows-python")

TARGETS = [
    {
        "name": "linux-x86_64",
        "zig_target": "x86_64-linux-gnu.2.17",
        "ext_suffix": ".cpython-313-x86_64-linux-gnu.so",
        "flags": ["-shared", "-fPIC"],
        "wheel_tag": "cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64",
        "include": "unix",
    },
    {
        "name": "macos-x86_64",
        "zig_target": "x86_64-macos",
        "ext_suffix": ".cpython-313-darwin.so",
        "flags": ["-shared", "-fPIC", "-undefined", "dynamic_lookup"],
        "wheel_tag": "cp313-cp313-macosx_10_13_x86_64",
        "include": "unix",
    },
    {
        "name": "macos-arm64",
        "zig_target": "aarch64-macos",
        "ext_suffix": ".cpython-313-darwin.so",
        "flags": ["-shared", "-fPIC", "-undefined", "dynamic_lookup"],
        "wheel_tag": "cp313-cp313-macosx_11_0_arm64",
        "include": "unix",
    },
    {
        "name": "windows-x86_64",
        "zig_target": "x86_64-windows-gnu",
        "ext_suffix": ".pyd",
        "flags": ["-shared"],
        "wheel_tag": "cp313-cp313-win_amd64",
        "include": "windows",
    },
]


def fetch_windows_python_libs() -> Path:
    """Download embeddable Python for Windows and extract python3.dll."""
    if (WINDOWS_PYTHON_DIR / "python313.dll").exists():
        print("[windows] Using cached python313.dll")
        return WINDOWS_PYTHON_DIR

    WINDOWS_PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = WINDOWS_PYTHON_DIR / "python-embed.zip"

    if not zip_path.exists():
        print(f"[windows] Downloading embeddable Python from {WINDOWS_PYTHON_URL}")
        urllib.request.urlretrieve(WINDOWS_PYTHON_URL, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(".dll") and "python" in name.lower():
                zf.extract(name, WINDOWS_PYTHON_DIR)
                print(f"[windows] Extracted {name}")

    return WINDOWS_PYTHON_DIR


def cythonize():
    """Run Cython to produce C source."""
    print(f"[cythonize] {PYX_SOURCE} -> {C_SOURCE}")
    subprocess.run(
        ["cython", str(PYX_SOURCE), "-o", str(C_SOURCE)],
        check=True,
    )


def compile_target(target: dict) -> Path:
    """Compile C source for a given target using zig cc."""
    name = target["name"]
    out_name = f"_fast{target['ext_suffix']}"
    build_dir = Path("build") / name
    build_dir.mkdir(parents=True, exist_ok=True)
    out_path = build_dir / out_name

    include_dir = INCLUDE_DIR / target["include"]

    extra_args = []
    if name == "windows-x86_64":
        win_dir = fetch_windows_python_libs()
        extra_args.append(str(win_dir / "python313.dll"))

    cmd = [
        "zig", "cc",
        "-target", target["zig_target"],
        *target["flags"],
        "-DNDEBUG",
        f"-I{include_dir}",
        "-o", str(out_path),
        str(C_SOURCE),
        *extra_args,
    ]

    print(f"[compile] {name}: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"[compile] {name}: -> {out_path}")
    return out_path


def record_hash(path: str, data: bytes) -> tuple[str, str, str]:
    """Compute hash and size for RECORD."""
    digest = hashlib.sha256(data).digest()
    hash_str = "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return (path, hash_str, str(len(data)))


def build_wheel(target: dict, ext_path: Path):
    """Package compiled extension into a wheel."""
    wheel_tag = target["wheel_tag"]
    wheel_name = f"{PROJECT_NAME}-{VERSION}-{wheel_tag}.whl"
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
        f"Requires-Python: >=3.13\n"
    ).encode()

    wheel_content = (
        f"Wheel-Version: 1.0\n"
        f"Generator: build.py\n"
        f"Root-Is-Purelib: false\n"
        f"Tag: {wheel_tag}\n"
    ).encode()

    top_level = b"zigcython\n"

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        # Package files
        pkg_init = "zigcython/__init__.py"
        whl.writestr(pkg_init, init_py)
        records.append(record_hash(pkg_init, init_py))

        ext_name = f"zigcython/_fast{target['ext_suffix']}"
        whl.writestr(ext_name, ext_data)
        records.append(record_hash(ext_name, ext_data))

        # dist-info files
        meta_path = f"{dist_info}/METADATA"
        whl.writestr(meta_path, metadata_content)
        records.append(record_hash(meta_path, metadata_content))

        wheel_path_in_zip = f"{dist_info}/WHEEL"
        whl.writestr(wheel_path_in_zip, wheel_content)
        records.append(record_hash(wheel_path_in_zip, wheel_content))

        top_level_path = f"{dist_info}/top_level.txt"
        whl.writestr(top_level_path, top_level)
        records.append(record_hash(top_level_path, top_level))

        # RECORD itself (no hash for itself)
        record_path = f"{dist_info}/RECORD"
        records.append((record_path, "", ""))
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in records:
            writer.writerow(row)
        whl.writestr(record_path, buf.getvalue())

    print(f"[wheel] {wheel_path}")
    return wheel_path


def main():
    # Clean
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    build_dir = Path("build")
    if build_dir.exists():
        shutil.rmtree(build_dir)

    # Step 1: Cythonize
    cythonize()

    # Step 2: Compile and package for each target
    wheels = []
    for target in TARGETS:
        ext_path = compile_target(target)
        wheel_path = build_wheel(target, ext_path)
        wheels.append(wheel_path)

    print(f"\nBuilt {len(wheels)} wheel(s):")
    for w in wheels:
        print(f"  {w}")


if __name__ == "__main__":
    main()
