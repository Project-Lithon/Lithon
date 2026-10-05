a: int[64] = 7
b: int[64] = 12

# The plain case: one value, chosen by a branch.
print(a if a < b else b)

# Nested: the inner conditional is itself an operand of the outer one.
print(a if a < b else (b if b < 100 else 0))

# Only the taken arm runs, so a call in the untaken arm must not happen.
def double(x: int[64]) -> int[64]:
    return x + x

print(double(5) if a > b else double(1))

# Floats survive the merge (this is the case that needs the promoted value's
# type, not just its id).
x: float[64] = 1.5
y: float[64] = 2.5
print(x if x < y else y)

# Inside a loop, so the value crosses a back edge as well as a join.
i: int[64] = 0
total: int[64] = 0
while i < 5:
    total = total + (i if i % 2 == 0 else -i)
    i = i + 1
print(total)

# As a function argument and on the right of an assignment, which is where a
# naive lowering that forgets to end the merge block falls over.
def three_times(x: int[64]) -> int[64]:
    return x + x + x

r: int[64] = three_times(b if a < b else a)
print(r)
