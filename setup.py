"""Setuptools configuration for native (non-cross) builds.

Used by ``uv build`` / ``pip install -e .`` to compile the Cython extension
for the host platform using the system compiler.

For cross-compilation to multiple platforms, use ``build.py`` directly.
"""

import os

from setuptools import Extension, setup
import numpy as np

# Use .pyx if available (editable / source tree), fall back to .c (sdist).
pyx_path = os.path.join("src", "zigcython", "_fast.pyx")
c_path = os.path.join("src", "zigcython", "_fast.c")

if os.path.exists(pyx_path):
    from Cython.Build import cythonize

    extensions = cythonize(
        [
            Extension(
                "zigcython._fast",
                [pyx_path],
                include_dirs=[np.get_include()],
                define_macros=[("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")],
            ),
        ],
    )
else:
    extensions = [
        Extension(
            "zigcython._fast",
            [c_path],
            include_dirs=[np.get_include()],
            define_macros=[("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")],
        ),
    ]

setup(ext_modules=extensions)
