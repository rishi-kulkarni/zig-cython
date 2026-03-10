project('zigcython', 'c', 'cython',
  version: '0.1.0',
  default_options: ['warning_level=0'],
)

if not meson.is_cross_build()
  # ── Native build (meson-python: uv build / pip install .) ──────────
  py = import('python').find_installation(pure: false)
  numpy_dep = dependency('numpy')

  py.extension_module(
    '_fast',
    'src' / 'zigcython' / '_fast.pyx',
    dependencies: numpy_dep,
    subdir: 'zigcython',
    install: true,
  )

  py.install_sources(
    'src' / 'zigcython' / '__init__.py',
    'src' / 'zigcython' / 'py.typed',
    subdir: 'zigcython',
  )
else
  # ── Cross-compilation (build.py: zig cc via cross-file) ────────────
  # build.py pre-cythonizes _fast.pyx → _fast.c and passes include
  # paths (Python + NumPy with platform-specific header overrides)
  # through the cross-file's c_args.
  shared_module('_fast',
    'src' / 'zigcython' / '_fast.c',
    c_args: ['-DNDEBUG'],
    name_prefix: '',
    install: false,
  )
endif
