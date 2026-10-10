"""Plain-CPython equivalent of test_codes_lithon/find_e.py.

Same program: seed e with the n=0 term (1/0! = 1), then sum 1/n! for
n = 1..7999 and print the result.
"""


def get_factorial(lim):
    fact = 1.0
    while lim > 1:
        fact = fact * lim
        lim = lim - 1
    return fact


e = 1.0  # the n=0 term: 1/0! = 1, seeded directly instead of summed
i = 0
while i < 8000:
    term = 1.0 / get_factorial(i + 1)
    e = e + term
    i = i + 1

print(e)