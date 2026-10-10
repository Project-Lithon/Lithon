"""Plain-CPython equivalent of test_codes_lithon/test_1b_fsum.py.

Same program -- accumulate i over range(1_000_000_000) and print the sum.
The only difference is the Lithon `int[64]` width annotations, which CPython's
int already provides natively and has no syntax for.

    CPython  ~188 s   ·   Lithon native  ~1.3 s
"""
i = 0
j = 0
for i in range(1000000000):
    j += i
print(j)