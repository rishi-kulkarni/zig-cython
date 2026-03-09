"""Setup for native builds via `uv build` / `pip install .`.

Cross-compilation for all 35 platform wheels is handled by build.py.
This file enables the standard PEP 517 build path so that:
  - `uv build` produces a native wheel + sdist
  - `uv pip install .` builds and installs from source
  - `uv pip install -e .` works for editable development
"""

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
