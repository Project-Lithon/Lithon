# 4.1. Fixed-capacity list: write, read, len.
#
# The annotation with no value is the DECLARATION. It is what reserves the run
# and zeroes it, so unlike a scalar annotation it is not a no-op that the type
# checker merely records -- drop it and the first IndexStore writes to a name
# that was never allocated.

xs: list[int[64], 6]

xs[0] = 10
xs[1] = 20
xs[2] = 30

print(xs[0])
print(xs[1])
print(xs[2])
# Nobody wrote this one, so it reads back as zero of its kind.
print(xs[5])
# N is part of the type, so len() is the capacity and cannot change at run time.
print(len(xs))

# An index computed at run time, to show the bound is checked against N and not
# against a literal the compiler happened to see.
i: int[64] = 4
xs[i] = 50
print(xs[i])

# A loop filling the whole run, which is the reason the elements are packed:
# element k lands at base + k * sizeof(T).
#
# This is a `while` and not `for k in range(...)` only to exercise the
# loop-carried accumulator merge alongside the list write; both forms work, and
# the range() lowering leaves `k` unannotated without that mattering, because
# every module is type checked now whether it annotates anything or not.
k: int[64] = 0
while k < len(xs):
    xs[k] = k * k
    k = k + 1

print(xs[0])
print(xs[1])
print(xs[4])
print(xs[5])