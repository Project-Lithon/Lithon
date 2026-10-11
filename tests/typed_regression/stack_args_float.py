def g(a: int[64], b: float[64], c: int[64]) -> float[64]:
    return b + c

def h(a: float[64], b: int[64], c: float[64]) -> float[64]:
    return a * 10.0 + c

def ff(a: float[64], b: float[64], c: float[64], d: float[64]) -> float[64]:
    return a + b + c + d

print(g(1, 2.5, 3))
print(h(1.5, 99, 2.5))
print(h(4.25, 0, 0.75))
print(ff(1.0, 2.0, 3.0, 4.0))
print(ff(-1.5, 2.5, -3.5, 4.5))