"""Cross-compile Cython extension to multiple platforms using Meson + Zig CC."""

import argparse
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
        "wheel_platform": "manylinux_2_17_x86_64.manylinux2014_x86_64",
        "include": "linux-64bit",
        "meson_system": "linux",
        "meson_cpu_family": "x86_64",
    },
    "manylinux-aarch64": {
        "zig_target": "aarch64-linux-gnu.2.17",
        "ext_suffix_platform": "aarch64-linux-gnu",
        "wheel_platform": "manylinux_2_17_aarch64.manylinux2014_aarch64",
        "include": "linux-aarch64",
        "meson_system": "linux",
        "meson_cpu_family": "aarch64",
    },
    "musllinux-x86_64": {
        "zig_target": "x86_64-linux-musl",
        "ext_suffix_platform": "x86_64-linux-musl",
        "wheel_platform": "musllinux_1_2_x86_64",
        "include": "linux-64bit",
        "meson_system": "linux",
        "meson_cpu_family": "x86_64",
    },
    "musllinux-aarch64": {
        "zig_target": "aarch64-linux-musl",
        "ext_suffix_platform": "aarch64-linux-musl",
        "wheel_platform": "musllinux_1_2_aarch64",
        "include": "linux-aarch64",
        "meson_system": "linux",
        "meson_cpu_family": "aarch64",
    },
    "macos-x86_64": {
        "zig_target": "x86_64-macos",
        "ext_suffix_platform": "darwin",
        "wheel_platform": "macosx_10_13_x86_64",
        "include": "macos-64bit",
        "meson_system": "darwin",
        "meson_cpu_family": "x86_64",
    },
    "macos-arm64": {
        "zig_target": "aarch64-macos",
        "ext_suffix_platform": "darwin",
        "wheel_platform": "macosx_11_0_arm64",
        "include": "macos-arm64",
        "meson_system": "darwin",
        "meson_cpu_family": "aarch64",
    },
    "windows-x86_64": {
        "zig_target": "x86_64-windows-gnu",
        "ext_suffix_platform": None,  # Windows uses .pyd
        "wheel_platform": "win_amd64",
        "include": "windows-64bit",
        "meson_system": "windows",
        "meson_cpu_family": "x86_64",
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


def _format_meson_array(items: list[str]) -> str:
    """Format a Python list as a Meson array literal."""
    inner = ", ".join(f"'{item}'" for item in items)
    return f"[{inner}]"


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
# Meson cross-file generation
# ---------------------------------------------------------------------------


def generate_cross_file(
    py_version: str, plat_name: str, plat: dict,
    python_inc: Path, numpy_inc: Path,
) -> Path:
    """Generate a Meson cross-file for one (Python version, platform) target."""
    c_args = [f"-I{python_inc.resolve()}", f"-I{numpy_inc.resolve()}"]

    link_args: list[str] = []
    if plat["meson_system"] == "darwin":
        link_args.extend(["-undefined", "dynamic_lookup"])
    if plat_name == "windows-x86_64":
        dll_path = fetch_windows_dll(py_version)
        link_args.append(str(dll_path))

    cross_file = BUILD_DIR / py_version / plat_name / "cross.ini"
    cross_file.parent.mkdir(parents=True, exist_ok=True)

    cpu = plat["meson_cpu_family"]
    cross_file.write_text(
        f"[binaries]\n"
        f"c = ['{ZIG}', 'cc', '-target', '{plat['zig_target']}']\n"
        f"ar = ['{ZIG}', 'ar']\n"
        f"ranlib = ['{ZIG}', 'ranlib']\n"
        f"\n"
        f"[built-in options]\n"
        f"c_args = {_format_meson_array(c_args)}\n"
        f"c_link_args = {_format_meson_array(link_args)}\n"
        f"\n"
        f"[host_machine]\n"
        f"system = '{plat['meson_system']}'\n"
        f"cpu_family = '{cpu}'\n"
        f"cpu = '{cpu}'\n"
        f"endian = 'little'\n"
    )
    return cross_file


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


def cythonize():
    """Run Cython to produce C source (consumed by meson.build cross path)."""
    print(f"[cythonize] {PYX_SOURCE} -> {C_SOURCE}")
    subprocess.run(
        ["cython", str(PYX_SOURCE), "-o", str(C_SOURCE)],
        check=True,
    )


def compile_target(py_version: str, plat_name: str, plat: dict) -> Path:
    """Compile C source for one (python_version, platform) via Meson."""
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

    # Generate Meson cross-file and run the build.
    cross_file = generate_cross_file(py_version, plat_name, plat, py_inc, np_inc)
    meson_dir = build_dir / "meson"

    setup_cmd = [
        "meson", "setup", str(meson_dir),
        "--cross-file", str(cross_file),
    ]
    if meson_dir.exists():
        setup_cmd.append("--wipe")

    print(f"[meson setup] {py_version}/{plat_name}")
    subprocess.run(setup_cmd, check=True)

    print(f"[meson compile] {py_version}/{plat_name}")
    subprocess.run(["meson", "compile", "-C", str(meson_dir)], check=True)

    # shared_module produces _fast.so (Linux/macOS) or _fast.dll (Windows).
    for candidate_ext in (".so", ".dll", ".dylib"):
        candidate = meson_dir / f"_fast{candidate_ext}"
        if candidate.exists():
            shutil.copy2(candidate, out_path)
            break
    else:
        raise FileNotFoundError(
            f"Could not find built module in {meson_dir}"
        )

    return out_path


def build_wheel(py_version: str, plat: dict, ext_path: Path) -> Path:
    """Stage wheel contents into a directory and let `wheel pack` assemble it."""
    tag = wheel_tag(py_version, plat)
    suffix = ext_suffix(py_version, plat)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # Stage directory: build/wheel-staging/<tag>/
    stage = BUILD_DIR / "wheel-staging" / tag
    if stage.exists():
        shutil.rmtree(stage)

    # Package files
    pkg_dir = stage / PROJECT_NAME
    pkg_dir.mkdir(parents=True)
    shutil.copy2("src/zigcython/__init__.py", pkg_dir / "__init__.py")
    shutil.copy2(ext_path, pkg_dir / f"_fast{suffix}")

    # dist-info with METADATA and WHEEL (RECORD is generated by wheel pack)
    dist_info = stage / f"{PROJECT_NAME}-{VERSION}.dist-info"
    dist_info.mkdir()

    # Build METADATA from pyproject.toml fields
    metadata_lines = [
        "Metadata-Version: 2.1",
        f"Name: {PROJECT_NAME}",
        f"Version: {VERSION}",
    ]
    if "description" in _project_meta:
        metadata_lines.append(f"Summary: {_project_meta['description']}")
    if "requires-python" in _project_meta:
        metadata_lines.append(f"Requires-Python: {_project_meta['requires-python']}")
    for dep in _project_meta.get("dependencies", []):
        metadata_lines.append(f"Requires-Dist: {dep}")
    (dist_info / "METADATA").write_text("\n".join(metadata_lines) + "\n")

    # WHEEL file — wheel pack reads Tag from here to determine the filename
    wheel_lines = [
        "Wheel-Version: 1.0",
        "Generator: build.py",
        "Root-Is-Purelib: false",
        f"Tag: {tag}",
    ]
    (dist_info / "WHEEL").write_text("\n".join(wheel_lines) + "\n")

    (dist_info / "top_level.txt").write_text(f"{PROJECT_NAME}\n")

    # Let the wheel package handle RECORD and zip creation
    subprocess.run(
        ["python", "-m", "wheel", "pack", str(stage), "--dest-dir", str(DIST_DIR)],
        check=True,
    )
    wheel_path = DIST_DIR / f"{PROJECT_NAME}-{VERSION}-{tag}.whl"
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
