"""Plain-CPython equivalent of test_codes_lithon/test_multi_ops.py.

Same program: 1e9 iterations of mixed integer arithmetic -- a multiply, a
bitwise xor, and a right shift -- accumulated into a total.
"""
n = 1_000_000_000
i = 0
total = 0

while i < n:
    total += (i * 31) ^ (i >> 3)
    i += 1

print(total)