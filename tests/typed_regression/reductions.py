# 6.1. The reduction helpers and list membership.
#
# sum / min / max / any / all and `in` are all rewrites over a list's
# compile-time capacity N: the walk is emitted as N straight-line steps, so
# there is no runtime helper, no iterator value and no unbounded memory. Every
# value printed here is checked against CPython's own answer (see the comments),
# and the interpreter and the native tier must agree byte for byte.

xs: list[int[64], 5]
xs[0] = 5
xs[1] = -2
xs[2] = 9
xs[3] = 1
xs[4] = -4

# CPython: sum([5,-2,9,1,-4]) == 9, min == -4, max == 9
s: int[64] = sum(xs)
print(s)
print(min(xs))
print(max(xs))

# A negative accumulator: sum([-1,-2,-3]) == -6
ns: list[int[64], 3]
ns[0] = -1
ns[1] = -2
ns[2] = -3
sn: int[64] = sum(ns)
print(sn)
print(min(ns))
print(max(ns))

# floats: sum([1.5,2.5,0.5]) == 4.5
fs: list[float[64], 3]
fs[0] = 1.5
fs[1] = 2.5
fs[2] = 0.5
sf: float[64] = sum(fs)
print(sf)

# bools: any([F,T,F]) == True, all([F,T,F]) == False
bs: list[bool, 3]
bs[0] = False
bs[1] = True
bs[2] = False
print(any(bs))
print(all(bs))

tbs: list[bool, 2]
tbs[0] = True
tbs[1] = True
print(any(tbs))
print(all(tbs))

# abs is scalar, not a walk: it is `-v if v < 0 else v`.
neg7: int[64] = -7
pos7: int[64] = 7
zero: int[64] = 0
print(abs(neg7))
print(abs(pos7))
print(abs(zero))

# `in` / `not in`: the OR of `x == xs[k]` over the compile-time N.
print(9 in xs)
print(7 in xs)
print(7 not in xs)
print(-4 in xs)

# ...and it is an ordinary bool, so it drives a branch.
if 5 in xs:
    print(1)
else:
    print(0)

# Membership over a float list.
print(2.5 in fs)
print(0.5 in fs)

# A one-element list is the smallest non-empty case.
one: list[int[64], 1]
one[0] = 42
print(sum(one))
print(min(one))
print(max(one))
print(42 in one)
print(43 in one)
