"""Plain-CPython equivalent of test_codes_lithon/DataTypes/convertions.lio.

Same program: explicit width conversions between int and float, plus a
32-bit integer that keeps its value. CPython's int/float are already the
widest widths, so only the width arguments are dropped.
"""
a = 24
b = 512
c = 22 / 7
k = 2147483647
print(a, b, c)
d = int(c)
e = float(a)
f = k
print(d, e, f)
