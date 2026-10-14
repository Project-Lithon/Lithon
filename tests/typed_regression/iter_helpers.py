# 6.1. enumerate and zip in a for header.
#
# Both are a frontend REWRITE over constructs that already exist: the pair
# (index, element) never becomes a value, so there is no new IR, no new opcode,
# and no new runtime helper. `for i, x in enumerate(xs)` becomes
# `for i in range(N): x = xs[i]`, and `for a, b in zip(xs, ys)` becomes the
# same loop bounded at min(N1, N2) -- a compile-time constant, because the
# declared capacity is what len() folds to.
#
# Every loop target below has a name of its own. That is not decoration: a name
# is expected to hold one type, so binding `b` to an int in one header and to a
# bool in another would leave it unresolved, and the print guard would (rightly)
# refuse to compile this file natively -- which is the one thing this file
# exists to check. Distinct names keep every value provably typed, so the file
# runs on the NATIVE tier and the tier-diff comparison is a real comparison of
# generated code against the interpreter.

xs: list[int[64], 5]
xs[0] = 10
xs[1] = 20
xs[2] = 30
xs[3] = 40
xs[4] = 50

# enumerate: the index is an ordinary int[64].
for ei, ex in enumerate(xs):
    print(ei)
    print(ex)

# zip stops at the SHORTEST input, like Python. Here `long6` has room for 6 and
# `short3` for 3, so the bound is 3 and long6[3..5] is never visited.
long6: list[int[64], 6]
short3: list[int[64], 3]
long6[0] = 1
long6[1] = 2
long6[2] = 3
long6[3] = 4
long6[4] = 5
long6[5] = 6
short3[0] = 7
short3[1] = 8
short3[2] = 9

total: int[64] = 0
for za, zb in zip(long6, short3):
    total = za + zb
print(total)

# zip is not limited to two, and the bound is min over all of them.
p: list[int[64], 2]
q: list[int[64], 2]
r: list[int[64], 2]
p[0] = 1
q[0] = 10
r[0] = 100
for zx, zy, zz in zip(p, q, r):
    print(zx)
    print(zy)
    print(zz)

# Element bindings keep the declared element type: a float, a bool and a narrow
# int (which stays signed -- -300 in an int[16] reads back -300).
fs: list[float[64], 2]
bs: list[bool, 2]
ns: list[int[16], 2]
fs[0] = 1.5
fs[1] = 2.5
bs[0] = True
bs[1] = False
ns[0] = -5
ns[1] = 300
for fi, fv in enumerate(fs):
    print(fv)
for bi, bv in enumerate(bs):
    print(bv)
for ni, nv in enumerate(ns):
    print(nv)

# The loop is a real loop: break and continue behave inside a zip header.
u: list[int[64], 4]
v: list[int[64], 4]
u[0] = 1
u[1] = 2
u[2] = 3
v[0] = 10
v[1] = 20
v[2] = 30
for ba, bb in zip(u, v):
    if ba == 2:
        continue
    if ba == 3:
        break
    print(ba)
    print(bb)
