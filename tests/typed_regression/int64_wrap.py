x: int[64] = 9223372036854775807
y: int[64] = 1
z: int[64] = wrap_add(x, y)
print(z)

a: int[64] = -9223372036854775808
b: int[64] = 1
c: int[64] = wrap_sub(a, b)
print(c)

p: int[64] = 3037000500
q: int[64] = 3037000500
r: int[64] = wrap_mul(p, q)
print(r)