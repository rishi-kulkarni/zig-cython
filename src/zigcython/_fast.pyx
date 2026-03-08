import numpy as np
cimport numpy as cnp

cnp.import_array()


def fib(int n):
    """Compute the nth Fibonacci number."""
    cdef int a = 0, b = 1, i
    for i in range(n):
        a, b = b, a + b
    return a


def cumsum(cnp.ndarray[cnp.float64_t, ndim=1] arr):
    """Compute cumulative sum of a 1D float64 array."""
    cdef int i
    cdef int n = arr.shape[0]
    cdef cnp.ndarray[cnp.float64_t, ndim=1] out = np.empty(n, dtype=np.float64)
    cdef double total = 0.0
    for i in range(n):
        total += arr[i]
        out[i] = total
    return out
