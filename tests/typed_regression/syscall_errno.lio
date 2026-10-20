# Phase III: the raw syscall primitive, exercised through the typed pipeline.
#
# Every value printed here is deterministic on both tiers and on every host,
# which is what lets this live in a suite with recorded expected output. The
# number is -1, which is not a syscall number: the kernel returns -ENOSYS, and
# the non-Linux trampoline returns the same -38. A real syscall like getuid(2)
# would print the invoking user's uid -- correct in both tiers, but a different
# number on a different host, so it cannot be recorded here.

def errno_of(n: int[64]) -> int[64]:
    r: int[64] = syscall(n)
    return r

bad: int[64] = errno_of(-1)
print(bad)

# The result is an ordinary int[64]. Nothing about it is special: arithmetic on
# a syscall return goes through the same ops as any other int.
shifted: int[64] = bad + 0
print(shifted)

# The syscall number may be computed rather than written as a literal, so the
# op takes values and not just constants.
base: int[64] = 0
unknown: int[64] = errno_of(base - 1)
print(unknown)
