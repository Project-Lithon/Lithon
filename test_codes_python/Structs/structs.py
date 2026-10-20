"""Plain-CPython equivalent of test_codes_lithon/Structs/structs.lio.

Same program: a struct with a bounded string field and an int field, then a
value of that struct with the fields assigned.

CPython has no width annotations, so the field types are dropped and the
class body just declares the names. Lithon packs the fields with no
padding; the field values are the same either way.
"""


class LithonStruct:
    name: str
    year: int


Lithon = LithonStruct()
Lithon.name = "Lithon"
Lithon.year = 2026

print(Lithon.name)
print(Lithon.year)
