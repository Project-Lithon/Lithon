"""Plain-CPython equivalent of
test_codes_lithon/test_eulars_constant_lim_def.py.

Same program: approximate e via the limit definition (1 + 1/k)**k, built up
as an explicit nested product since Lithon has no `**` operator, keeping the
largest value reached over k = 1..n and printing the result.
"""
n = 1000
i = 0
j = 0
k = 0
e = 0.0
le = 0.0

for i in range(n):
    k = i + 1
    e = 1.0
    for j in range(k):
        e = e * (1.0 + 1.0 / k)
    if e > le:
        le = e

print(le)