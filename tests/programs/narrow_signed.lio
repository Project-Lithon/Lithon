# 4.6. A Lithon int[w] is SIGNED on every path it can be read back: a negative
# int[8]/int[16]/int[32] stored in a list element, a struct field, or behind a
# pointer must read back negative, not as its zero-extended bit pattern.
#
# Before this was pinned the two tiers disagreed -- list[int[16]] element -5
# printed -5 under the interpreter and 65531 natively (the JIT zero-extended),
# while a struct int[16] field printed 65531 in BOTH tiers (the interpreter
# zero-extended too). Both tiers now sign-extend a narrow int load, and bool
# keeps its 0/1 zero-extension. CPython cannot run this file (Struct and the
# int[w] spellings are Lithon's own), so expected/narrow_signed.out is written
# from the documented signed semantics rather than recorded from CPython.


class Packed(Struct):
    a: int[8]
    b: int[16]
    c: int[32]
    d: int[64]


# The same negative values through the three read-back paths: a list element,
# a struct field, and a pointer dereference. Each lane width gets one.
ps: list[int[16], 4]
ps[0] = -5
ps[1] = -300
ps[2] = 32767
ps[3] = -32768
print(ps[0])
print(ps[1])
print(ps[2])
print(ps[3])

s: Packed
s.a = -3
s.b = -300
s.c = -70000
s.d = -5
print(s.a)
print(s.b)
print(s.c)
print(s.d)

n: int[16] = -9
_pn: ptr[int[16]] = addressof(n)
print(valueof(_pn))
m: int[8] = -1
_pm: ptr[int[8]] = addressof(m)
print(valueof(_pm))
k: int[32] = -100000
_pk: ptr[int[32]] = addressof(k)
print(valueof(_pk))

# Reading a narrow field back and using it must use the signed value:
# -300 + 100 = -200, not 65236 + 100.
print(s.b + 100)

# A narrow field written from a non-constant source (a list element), so the
# sign-extending load is not constant-folded to a literal.
ps[0] = -4
s.b = ps[0]
print(s.b)
