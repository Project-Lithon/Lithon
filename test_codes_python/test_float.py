"""Plain-CPython equivalent of test_codes_lithon/test_float.py.

Same program: a zero-argument function returning float[64] that adds 1.0 to
a local, called three times. CPython's float is already binary64, so only
the width annotation is dropped.
"""


def f():
    x = 1.0
    x = x + 1.0
    return x


print(f())
print(f())
print(f())