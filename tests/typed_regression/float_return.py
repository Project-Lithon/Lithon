# A float[64]-returning function, called and printed. This file exists because
# NOTHING in tests/programs/ or tests/typed_regression/ called a function that
# returns a float, which is exactly the gap that let an ABI bug ship: both the
# Op::Return emission and the Op::Call result capture read RAX unconditionally,
# while a float[64] return travels in XMM0. Calling such a function printed a
# different near-zero denormal on each call, because the caller re-read stale
# RAX bits as a double -- and it passed the existing suite, which had no test
# that could see it.
#
# Every call here is repeated on purpose. A single call can land on a stack slot
# that happens to hold the right bits by luck; three in a row is what makes the
# stale-read reproducible rather than intermittent.
def half() -> float[64]:
    return 2.0

def quarter() -> float[64]:
    return 0.25

print(half())
print(half())
print(half())
print(quarter())

# The returned double must be usable as a VALUE, not only printable: the
# consumer is ordinary float arithmetic, so the result has to arrive in XMM0
# and then flow on into the SSE add path.
print(half() + quarter())

# An int-returning function alongside a float one, so the RAX path stays
# covered in the same program and the two cannot be confused for one another.
def seven() -> int[64]:
    return 7

print(seven())
print(seven() + 1)

# Float ARGUMENTS, which were broken for the same underlying reason and are a
# separate defect from the return path: a float[64] parameter arrives in XMM0,
# but the prologue spilled every parameter from a GP argument register, so the
# parameter's frame slot held unrelated integer bits. `return x` then handed back
# a denormal built from those bits -- 0.0 where 9.75 went in.
def ident(x: float[64]) -> float[64]:
    return x

def dbl(x: float[64]) -> float[64]:
    y: float[64] = x + x
    return y

print(ident(9.75))
print(ident(0.5))
print(dbl(1.25))

# Mixed kinds in one call, so a float argument cannot be assumed to be the only
# kind and the GP and XMM paths have to coexist in the same marshalling loop.
def mix(a: int[64], b: float[64]) -> float[64]:
    n: int[64] = a
    return b

print(mix(3, 2.5))

# A float parameter used enough times that the register allocator's promotion
# pass takes an interest in it. A promoted float parameter must not be pinned to
# a GP register: the prologue would emit `mov <gp_reg>, rdi` and read an
# unrelated integer's low 64 bits as the double.
def many(x: float[64]) -> float[64]:
    a: float[64] = x + x
    b: float[64] = a + a
    c: float[64] = b + b
    d: float[64] = c + x
    return d

print(many(1.5))

# Two consecutive float arguments, so both kFloatArgRegs[0] and kFloatArgRegs[1]
# are exercised. Only testing a float in one position would have left the second
# XMM slot completely unverified.
def add2(a: float[64], b: float[64]) -> float[64]:
    c: float[64] = a + b
    return c

s: float[64] = add2(1.5, 2.25)
print(s)
print(add2(s, s))
