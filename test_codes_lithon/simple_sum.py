# simple_sum.py
#
# `int()` and `input()` are not supported yet (the frontend lowers the string
# literal in input() to "expression node Constant not supported yet"), and
# `len()` on a string is unavailable for the same reason. So operands are
# fixed literals and the sum is over a typed list instead of a string.
xs: list[int[64], 4]

xs[0] = 10
xs[1] = 20
xs[2] = 30
xs[3] = 40

total: int[64] = 0
k: int[64] = 0
while k < len(xs):
    total = total + xs[k]
    k = k + 1

print(total)
print(len(xs))