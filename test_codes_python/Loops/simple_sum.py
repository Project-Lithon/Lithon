"""Plain-CPython equivalent of test_codes_lithon/Loops/simple_sum.lio.

Same program: fill a fixed-size list of four, sum it with a while loop, then
print the total and the length. CPython lists are unbounded, so the capacity
argument is dropped; the loop still runs over the same four elements.
"""
xs = [0, 0, 0, 0]

xs[0] = 10
xs[1] = 20
xs[2] = 30
xs[3] = 40

total = 0
k = 0
while k < len(xs):
    total = total + xs[k]
    k = k + 1

print(total)
print(len(xs))
