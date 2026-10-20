# 4.7. Enums: a Rust-style tagged union stored as one packed byte blob.
#
# An enum is storage, exactly like a struct, so CPython cannot run this file
# (`Enum` is not a Python builtin) and expected/enum_basic.out is written from
# the documented 4.7 semantics rather than recorded from a CPython run. The
# layout is `[tag][payload]` with no padding: the tag is an int[8] while the
# enum has 128 variants or fewer, the payload starts at the tag width, and the
# total size is the tag width plus the largest payload. Inactive payload bytes
# are undefined and are never read.
#
# `s: Shape` emits a valueless `store s : struct[9]` (one tag byte plus the
# int[64] Rect payload). A construction writes the tag and the active payload;
# a `match` reads the tag and, inside the chosen arm, the payload. Both lower to
# the same fieldload/fieldstore a struct field uses.


class Shape(Enum):
    Empty
    Dot
    Line: int[32]
    Rect: int[64]


# An exhaustive match: all four variants are covered, so no `case _:` is needed.
s: Shape
s = Shape.Dot
match s:
    case Shape.Empty:
        print(0)
    case Shape.Dot:
        print(1)
    case Shape.Line(n):
        print(n)
    case Shape.Rect(w):
        print(w)

# A scalar payload is read back through the arm's binding. int[32] is signed, so
# a negative payload reads back negative.
s = Shape.Line(42)
match s:
    case Shape.Empty:
        print(0)
    case Shape.Dot:
        print(1)
    case Shape.Line(n):
        print(n)
    case Shape.Rect(w):
        print(w)

s = Shape.Rect(1000)
match s:
    case Shape.Empty:
        print(0)
    case Shape.Dot:
        print(1)
    case Shape.Line(n):
        print(n)
    case Shape.Rect(w):
        print(w)

# Overwriting a payload variant with a unit variant makes the old payload bytes
# inactive. They are never read: the Empty arm has no binding, so the stale
# 7 cannot leak out.
s = Shape.Line(7)
s = Shape.Empty
match s:
    case Shape.Empty:
        print(-1)
    case Shape.Line(n):
        print(n)
    case _:
        print(-2)


# A narrow int[8] and a bool payload, to pin the signed narrow readback and the
# 0/1 bool path through an enum payload.
class Flag(Enum):
    Set: bool
    Code: int[8]


f: Flag
f = Flag.Code(-5)
match f:
    case Flag.Set(b):
        print(b)
    case Flag.Code(c):
        print(c)

f = Flag.Set(True)
match f:
    case Flag.Set(b):
        print(b)
    case Flag.Code(c):
        print(c)

# A float[64] payload goes through the double path, so a negative value is exact.
class Sample(Enum):
    None_
    Value: float[64]


g: Sample
g = Sample.Value(-2.25)
match g:
    case Sample.Value(x):
        print(x)
    case Sample.None_:
        print(0.0)
