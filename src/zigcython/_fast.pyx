def fib(int n):
    """Compute the nth Fibonacci number."""
    cdef int a = 0, b = 1, i
    for i in range(n):
        a, b = b, a + b
    return a
