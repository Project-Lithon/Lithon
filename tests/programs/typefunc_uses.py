# 4.8. Where a type function is applied. Because a call is resolved inside
# `parse_type_annotation`, the single choke point every annotation flows
# through, a type function works anywhere an annotation is allowed: nested in
# another type function's argument, in a function parameter and return type, as
# a struct field, and as a container element.
#
# CPython cannot run this file, so expected/typefunc_uses.out is written from
# the documented 4.8 semantics.


def Word() -> NewType:
    return {int[32]}


def Pair(t) -> NewType:
    return {list[t, 2]}


def Wide() -> NewType:
    return {int[64]}


# Nested: the argument to Pair is itself a type-function call, so Pair(Word())
# is list[int[32], 2].
ps: Pair(Word())
ps[0] = 11
ps[1] = -22
print(ps[0] + ps[1])


# In a signature: parameter and return types.
def addtwo(x: Wide(), y: Wide()) -> Wide():
    return x + y


print(addtwo(700, -1000))


# As a struct field type.
class Rec(Struct):
    code: Word()
    flag: bool


r: Rec
r.code = 70000
r.flag = True
print(r.code)
print(r.flag)

# As a container element type.
zs: list[Word(), 3]
zs[0] = 7
zs[1] = 8
zs[2] = 9
print(zs[0] + zs[1] + zs[2])
