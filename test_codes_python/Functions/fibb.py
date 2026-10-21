"""Plain-CPython equivalent of test_codes_lithon/Functions/fibb.lio.

Same program: the recursive Fibonacci function with an explicit parameter
and return type, called with 30. CPython has no width annotations, so only
the annotations are dropped.
"""


def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


x = fib(30)
print(x)
