x: int[64] = 5
_p: ptr[int[64]] = addressof(x)
print(valueof(_p))
q: int[64] = 6
_q: ptr[int[64]] = addressof(q)
print(valueof(_q))
print(_p == _q)
_r: ptr[int[64]] = addressof(x)
print(_p == _r)
print(_p == 0)
z: int[64] = valueof(_p)
print(z)
_p = _q
print(valueof(_p))
w: int[64] = 7
_w: ptr[int[64]] = addressof(w)
print(_w == _q)
t: int[64] = 3
_t: ptr[int[64]] = addressof(t)
print((_t + 1) - 1 == _t)
f: float[64] = 2.5
_pf: ptr[float[64]] = addressof(f)
print(valueof(_pf))
_f2: ptr[float[64]] = _pf + 2 - 2
print(_f2 == _pf)
f = 3.25
print(valueof(_pf))
n: int[8] = 7
_pn: ptr[int[8]] = addressof(n)
print(valueof(_pn))
b: bool = True
_pb: ptr[bool] = addressof(b)
print(valueof(_pb))
b = False
print(valueof(_pb))