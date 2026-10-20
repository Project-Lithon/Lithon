# 4.9. Methods on an enum. The receiver `self` is the enum's storage, so a
# method reads it with `match self` (the only way to read an enum, 4.7) and can
# return a scalar computed from the active variant's payload. A method on an
# enum cannot take or return the enum itself: a method signature is scalars
# only, and an enum is storage. CPython cannot run this file (`Enum` is not a
# Python builtin), so expected/method_enum.out is written from the documented
# semantics; both tiers agree.


class Shape(Enum):
    Empty
    Dot
    Line: int[32]

    # A value-returning method that matches the receiver and returns a scalar.
    def code(self) -> int[64]:
        match self:
            case Shape.Empty:
                return 0
            case Shape.Dot:
                return 1
            case Shape.Line(n):
                return n
            case _:
                return -1

    # A method can also take a parameter and branch on the payload.
    def at_least(self, thr: int[32]) -> bool:
        match self:
            case Shape.Line(n):
                return n > thr
            case _:
                return False

    # A void method that does not read the payload; the wildcard covers the rest.
    def describe(self):
        match self:
            case Shape.Empty:
                print(100)
            case Shape.Dot:
                print(101)
            case _:
                print(102)


s: Shape
s = Shape.Empty
print(s.code())
print(s.at_least(3))
s.describe()

s = Shape.Dot
print(s.code())
print(s.at_least(3))
s.describe()

s = Shape.Line(42)
print(s.code())
print(s.at_least(3))
s.describe()
