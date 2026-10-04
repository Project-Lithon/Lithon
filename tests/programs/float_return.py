# Functions that RETURN a float, called and then used in arithmetic.
#
# This file exists because nothing in tests/programs/ or tests/typed_regression/
# called a float-returning function, and that is precisely the hole this bug fell
# through: Op::Return and the Op::Call result-capture both read RAX
# unconditionally, while a float return travels in XMM0 under SysV and Win64.
# Calling such a function printed a different near-zero denormal each time,
# because the caller reinterpreted whatever stale bits sat in RAX as a double.
#
# It went unnoticed for a specific reason worth recording:
# tests/typed_regression runs through build/hello, which is interpreter-only, so
# a typed_regression program can NEVER catch a native-codegen bug at all. It sits
# in tests/programs/ instead, because tools/run_tier_diff.py drives that
# directory through build/tier_runner and compares native output against the
# interpreter. Keeping it annotation-free is what lets run_regression.py also
# check it against real CPython.
def half():
    return 2.0

def quarter():
    return 0.25

def seven():
    return 7

print(half())
print(half())
print(half())
print(quarter())

# The captured value has to be usable as a double, not merely printable: this
# consumes it in arithmetic, where stale bits cannot cancel out.
print(half() + quarter())

# An int-returning function next to the float ones, so the RAX path stays
# covered in the same program and the two can never be confused for each other.
print(seven())
print(seven() + 1)

# Unannotated float PARAMETERS. Fixing only the declared-type case left this
# broken: with no `float[64]` annotation the prologue could not tell an untyped
# `float` parameter from an `int` one, so it spilled from a GP argument register
# while the caller had correctly marshalled the double into XMM. The parameter
# kind is now inferred from call sites, which is why these are still
# annotation-free and stay runnable by real CPython.
def ident2(x):
    return x

def dbl2(x):
    return x + x

def add2u(a, b):
    return a + b

print(ident2(9.75))
print(dbl2(1.25))
print(add2u(1.5, 2.25))

# An untyped int parameter next to them, so inference cannot silently turn every
# parameter into a float.
def dbli(x):
    return x + x

print(dbli(9))
