#ifndef Py_PYCONFIG_H
#define Py_PYCONFIG_H

/* Minimal pyconfig.h for cross-compiled Cython extensions (Unix LP64) */

/* Type sizes */
#define SIZEOF_DOUBLE 8
#define SIZEOF_FLOAT 4
#define SIZEOF_FPOS_T 16
#define SIZEOF_INT 4
#define SIZEOF_LONG 8
#define SIZEOF_LONG_DOUBLE 16
#define SIZEOF_LONG_LONG 8
#define SIZEOF_OFF_T 8
#define SIZEOF_PID_T 4
#define SIZEOF_PTHREAD_KEY_T 4
#define SIZEOF_PTHREAD_T 8
#define SIZEOF_SHORT 2
#define SIZEOF_SIZE_T 8
#define SIZEOF_TIME_T 8
#define SIZEOF_UINTPTR_T 8
#define SIZEOF_VOID_P 8
#define SIZEOF_WCHAR_T 4
#define SIZEOF__BOOL 1

#define ALIGNOF_LONG 8
#define ALIGNOF_SIZE_T 8
#define ALIGNOF_MAX_ALIGN_T 16
#define LONG_BIT 64

/* Endianness */
#define DOUBLE_IS_LITTLE_ENDIAN_IEEE754 1
#define HAVE_ENDIAN_H 1

/* Standard headers */
#define STDC_HEADERS 1
#define HAVE_DLFCN_H 1
#define HAVE_ERRNO_H 1
#define HAVE_FCNTL_H 1
#define HAVE_PTHREAD_H 1
#define HAVE_SIGNAL_H 1
#define HAVE_STDDEF_H 1
#define HAVE_UNISTD_H 1
#define HAVE_WCHAR_H 1
#define HAVE_SYS_STAT_H 1
#define HAVE_SYS_TIME_H 1
#define HAVE_SYS_TYPES_H 1

/* Python config */
#define HAVE_CLOCK_GETTIME 1
#define HAVE_DLOPEN 1
#define HAVE_DYNAMIC_LOADING 1
#define HAVE_FORK 1
#define HAVE_SIGACTION 1
#define WITH_PYMALLOC 1
#define WITH_DOC_STRINGS 1
#define HAVE_HYPOT 1

/* Fixed-width integer types */
#define PY_INT32_T int32_t
#define PY_INT64_T int64_t
#define PY_UINT32_T uint32_t
#define PY_UINT64_T uint64_t

#endif /* Py_PYCONFIG_H */
