# 4.6. Structs: a named run of typed fields in one packed byte blob.
#
# CPython cannot run `class Point(Struct)` -- Struct is not a Python builtin --
# so this file has no CPython oracle and expected/struct_basic.out is written
# from the documented 4.6 semantics rather than recorded from a CPython run.
# This is the same split the dict and pointer tests use for the one construct
# whose spelling is Lithon's own.
#
# The declaration `p: Point` emits a valueless `store p : struct[8]`, which is
# what reserves and zeroes the bytes, exactly as a list declaration reserves its
# element run. A field is not a value and a struct is not a value: the only
# operations are field reads and field writes, and each is compiled to a load or
# store at a fixed byte offset the frontend computed.


class Point(Struct):
    x: int[64]
    y: int[64]


# A nested struct field is packed at its own offset with no padding, so `family`
# at 0, `port` at 2, `addr` at 4 and `active` at 12 make a 13 byte struct.
class SockAddrIn(Struct):
    family: int[16]
    port: int[16]
    addr: int[64]
    active: bool


class Vec(Struct):
    x: float[64]
    y: float[64]


# The declaration reserves and zeroes. Writing a field is a partial write into
# that run, not a rebinding of the name.
p: Point
p.x = 3
p.y = 4
print(p.x)
print(p.y)

s: SockAddrIn
s.family = 2
s.port = 80
print(s.family)
print(s.port)
# Nobody wrote these two fields, so they read back as zero of their kind: 0 for
# an int and False for a bool. This is the struct analogue of an unwritten list
# element reading zero.
print(s.addr)
print(s.active)

# A 64-bit field holds a value no 16-bit field could, which is what makes the
# offsets matter rather than a single shared cell.
s.addr = 16909060
s.active = True
print(s.addr)
print(s.active)

# Float fields go through the double path, so an arithmetic read is exact and a
# negative value survives (a float[64] field is always the full eight bytes).
v: Vec
v.x = 1.5
v.y = -2.25
print(v.x)
print(v.y)
print(v.x + v.y)

# A field written and read inside a loop, so the struct is live across a merge
# and its bytes are not a compile-time constant.
i: int[64] = 0
total: int[64] = 0
while i < 3:
    p.x = i
    total = total + p.x
    i = i + 1
print(total)
print(p.x)
