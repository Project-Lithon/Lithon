"""Plain-CPython equivalent of test_codes_lithon/DataTypes/floats.lio.

Same program: print a float, print a float in exponent form, and print the
classic 0.1 + 0.2 rounding artefact. CPython's float is already binary64,
so only the width annotation is dropped.
"""
print(1.0)
print(1e22)
print(0.1 + 0.2)
