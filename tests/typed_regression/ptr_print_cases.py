# Pointer printing cases for tools/check_ptr_print.py. NOT part of tests/programs:
# that suite demands byte-identical stdout across tiers, and a printed address
# legitimately differs (interpreter: synthetic, native: a real frame address).
a: int[64] = 1
b: int[64] = 2
_a: ptr[int[64]] = addressof(a)
_a2: ptr[int[64]] = addressof(a)
_b: ptr[int[64]] = addressof(b)
print(_a == _a2)            # True  -- same variable, two addressof() calls
print(_a == _b)               # False -- different variables
print((_a + 1) - 1 == _a)      # True  -- round-trip arithmetic, same idiom as ptr_basic.py
print(valueof(_a))
print(_a == _a2)
f: float[64] = 2.5
_f: ptr[float[64]] = addressof(f)
print((_f + 1) - 1 == _f)
print(valueof(_f))
n: int[8] = 9
_n: ptr[int[8]] = addressof(n)
print((_n + 1) - 1 == _n)
print(valueof(_n))
