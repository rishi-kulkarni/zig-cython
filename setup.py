"""Setup for native builds via `uv build` / `pip install .`.

Cross-compilation for all 35 platform wheels is handled by build.py.
This file enables the standard PEP 517 build path so that:
  - `uv build` produces a native wheel + sdist
  - `uv pip install .` builds and installs from source
  - `uv pip install -e .` works for editable development

Uses Zig as the C compiler so no system compiler is needed.
"""

import os
from pathlib import Path

import ziglang

ZIG_CC = str(Path(ziglang.__path__[0]) / "zig") + " cc"
os.environ.setdefault("CC", ZIG_CC)
os.environ.setdefault("LDSHARED", ZIG_CC + " -shared")

from setuptools import Extension, setup
from Cython.Build import cythonize
import numpy

setup(
    ext_modules=cythonize(
        [
            Extension(
                "zigcython._fast",
                ["src/zigcython/_fast.pyx"],
                include_dirs=[numpy.get_include()],
            )
        ],
    ),
)
