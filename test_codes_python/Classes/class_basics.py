"""Plain-CPython equivalent of test_codes_lithon/Classes/class_basics.lio.

Same program: a class with two typed fields, then a value of that class with
the fields assigned. CPython has no width annotations, so the class body just
declares the names and the assignments follow.

Note: Lithon requires a class to derive from Struct or Enum; CPython has no
such requirement, so the portable subset drops it.
"""


class test_class:
    name: str
    year: int


x = test_class()
x.name = "Lithon"
x.year = 2026

print(x.name)
print(x.year)
