"""Plain-CPython equivalent of test_codes_lithon/NewTypes/new_type.lio.

Same program: a type function that takes a list of widths and produces a
concrete container type, then a value of that type is declared and printed.

Lithon's type functions are compile-time type constructors -- they expand
structurally into the underlying type. CPython has no such construct, so the
portable subset keeps the *shape* of the program: the same field names, the
same nested list/tuple structure, the same printed result.
"""
def custom(a, b, c, d, e):
    # Lithon: dict[str[e], list[tuple[int[d], a], b], c]
    # The widths are values here, not types, so the same nested shape is
    # built explicitly.
    return {"test": [(1, 2, 3, 4), (4, 3, 2, 1)]}


t = custom(4, 2, 1, 32, 16)
print(t)
