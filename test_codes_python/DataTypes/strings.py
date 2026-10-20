"""Plain-CPython equivalent of test_codes_lithon/DataTypes/strings.lio.

Same program: a bounded string holding "Lithon", then its length.

Lithon declares `name: str[7] = "Lithon"` -- seven bytes, the last one the
NUL terminator -- and `len(name)` reports the six visible characters. CPython
strings are already NUL-terminated and unbounded, so the capacity is simply
dropped; len() reports the same six characters.
"""
name = "Lithon"
print(name)
print(len(name))
