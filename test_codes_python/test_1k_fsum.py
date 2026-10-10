"""Plain-CPython equivalent of test_codes_lithon/test_1k_fsum.py.

Same program over 1,000 iterations -- tiny workload, used for checking the
benchmark UI and video recorder rather than for real speedup numbers.
"""
i = 0
j = 0
for i in range(1000):
    j += i
print(j)