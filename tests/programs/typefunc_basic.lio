# 4.8. Type functions: a structural typedef -- `def Name(p, ...) -> NewType:
# return { T }`. Applying `Name(args)` substitutes the arguments into T and
# yields an ordinary type, indistinguishable from the expansion spelled out.
# The definitions emit no code and nothing about them survives into the IR.
#
# `typefunc_basic` pins the four shapes a type function is most useful for: a
# container (`list[t, n]`), a scalar alias, a pointer, and a pure identity
# alias whose whole point is that the expansion IS the spelling. CPython cannot
# run this file (`NewType` is not a value), so expected/typefunc_basic.out is
# written from the documented 4.8 semantics.


def Fixed(t, n) -> NewType:
    return {list[t, n]}


def Word() -> NewType:
    return {int[32]}


def Alias(t) -> NewType:
    return {t}


def Ref(t) -> NewType:
    return {ptr[t]}


# A container typedef: `Fixed(int[64], 4)` is `list[int[64], 4]`.
xs: Fixed(int[64], 4)
xs[0] = 100
xs[1] = 23
xs[2] = -3
print(xs[0] + xs[1] + xs[2])

# A scalar typedef: `Word()` is `int[32]`.
w: Word() = 1000
print(w)

# A pure alias: `Alias(int[16])` is `int[16]`.
y: Alias(int[16]) = -5
print(y)

# Structural: Alias(int[64]) is the very same type as int[64], so declaring `a`
# once each way is not a re-declaration with a different type.
a: int[64] = 5
a: Alias(int[64]) = 6
print(a)

# A pointer typedef: `Ref(int[64])` is `ptr[int[64]]`.
f: int[64] = 42
_p: Ref(int[64]) = addressof(f)
print(valueof(_p))
f = 7
print(valueof(_p))
