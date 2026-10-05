def get_factorial(lim: int[64]) -> float[64]:
    fact: float[64] = 1.0
    while lim > 1:
        fact = fact * lim
        lim = lim - 1
    return fact

e: float[64] = 1.0   # <-- the n=0 term: 1/0! = 1, seeded directly instead of summed
i: int[64] = 0
while i < 8000:
    term: float[64] = 1.0 / get_factorial(i + 1)
    #print(term)
    e = e + term
    i = i + 1

print(e)
