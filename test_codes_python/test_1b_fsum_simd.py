"""Plain-CPython equivalent of test_codes_lithon/test_1b_fsum_simd.py.

Same program: 1e9 iterations of a float[64] accumulator, adding 0.5 each
time. Lithon runs this through the auto-vectorizer's AVX2 path.
"""
i = 0
j = 0.0
for i in range(1000000000):
    j += 0.5
print(j)