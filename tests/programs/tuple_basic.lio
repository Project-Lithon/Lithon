# 4.2. A tuple: constructed by a literal, then read only.
#
# The type is homogeneous and immutable, and those are the only two things that
# separate it from a list. The layout is identical, with a packed stride, element
# 0 at offset 0 and no length field, so every read below would work the same on
# a list.
#
# Immutability is not a runtime property. A tuple literal fills the run through
# the ordinary IndexStore path, and after that the type checker refuses every
# further store. So there is nothing to test here about immutability at run
# time, because the illegal program never becomes machine code.
#
# Every read below has a CPython equivalent, so expected/tuple_basic.out is a
# real oracle rather than a recording of whatever this tier happened to print.

# A tuple built from a literal. The valueless store of the declaration reserves
# and zeroes the run first, so a literal is a fill, not an allocation trick.
t: tuple[int[64], 4] = (10, 20, 30, 40)
print(t[0])
print(t[3])

# A bare declaration, with no literal, is legal and is the all zero tuple. This
# is the case that makes the immutability rule non trivial. Construction is
# partially written or not written, never written some other way.
z: tuple[int[64], 3]
print(z[0])
print(z[2])

# Literal elements are ordinary expressions, evaluated before the store, so they
# can be anything the expression language already accepts.
a: int[64] = 3
b: int[64] = 4
sums: tuple[int[64], 2] = (a + b, a * b)
print(sums[0])
print(sums[1])

# float and bool elements, to show the element type is carried rather than
# assumed. A float tuple prints a float and a bool tuple prints a keyword.
fs: tuple[float[64], 3] = (1.5, 2.5, 3.0)
print(fs[0])
print(fs[2])
bs: tuple[bool, 2] = (True, False)
print(bs[0])
print(bs[1])

# A runtime index, which is the case where the bounds check has to be emitted
# rather than folded. N is static, so a literal index is decided at compile time
# and this one is not.
i: int[64] = 2
print(t[i])

# Reading a tuple inside a loop, so the merge at the loop header carries a
# promoted tuple read across iterations.
acc: int[64] = 0
k: int[64] = 0
while k < 4:
    acc = acc + t[k]
    k = k + 1
print(acc)