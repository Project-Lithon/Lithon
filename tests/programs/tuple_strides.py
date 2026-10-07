# 4.2. A tuple is packed exactly like a list. Element k is at base plus k times
# the size of T.
#
# A tuple cannot be filled by assignment, so the way to pin the stride is to give
# every slot a different value in the literal and then read them all back. A
# uniform 8 byte stride would still return plausible numbers for the int cases,
# which is what makes this worth doing, so the bool tuple is the sharpest probe.
# Its stride is 1 byte, and reading 8 instead of 1 lands inside the frame where
# whatever happens to live, not on False.
#
# There is no CPython oracle for int[16] or int[32]. Python has no such types,
# and int[8] would wrap where CPython does not. expected/tuple_strides.out is
# therefore recorded from the native tier, and the interpreter is required to
# agree with it rather than the other way round.

ns: tuple[int[32], 4] = (11, 22, 33, 44)
ss: tuple[int[16], 3] = (300, 301, 302)
bs: tuple[bool, 4] = (True, False, True, False)
fs: tuple[float[64], 3] = (1.5, 2.5, 3.5)

print(ns[0])
print(ns[1])
print(ns[3])
print(ss[0])
print(ss[2])
print(bs[0])
print(bs[1])
print(bs[2])
print(bs[3])
print(fs[0])
print(fs[2])
