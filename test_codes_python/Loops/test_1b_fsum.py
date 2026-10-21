"""Plain-CPython equivalent of test_codes_lithon/Loops/test_1b_fsum.lio.

Same program -- accumulate i over range(1,000,000,000) and print the sum.
The only difference is the Lithon `int[64]` width annotations, which CPython's
int already provides natively and has no syntax for.
"""
i = 0
j = 0
for i in range(1000000000):
    j += i
print(j)
