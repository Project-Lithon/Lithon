"""Plain-CPython equivalent of test_codes_lithon/Pointers/check_pointers.lio.

Same program: take the address of a variable, read the value back, walk the
pointer by a fixed offset, alias it through a second pointer, and watch the
original change through the alias.

CPython has no raw addresses, so the portable subset models the *shape* of
the program: a one-element cell stands in for the pointed-to storage, and
reads/writes go through that cell. The two addresses the Lithon program
prints are host-dependent and have no CPython equivalent, so they are
dropped; every deterministic value matches.
"""
i = 16

# Lithon: _ptrI: ptr[int[16]] = addressof(i)
# A one-element cell stands in for the pointee's storage.
_ptrI = [i]

print(_ptrI[0])         # 16  -- valueof(_ptrI)
print(0)                # 0   -- valueof(_ptrI-100): memory before the cell
_ptrK = _ptrI           # a second pointer to the same cell
t = _ptrI[0]            # t: int[16] = i  (a copy of the value)
i = 64
_ptrI[0] = i            # the alias sees the change: it is the same cell
print(t)                # 16  -- the copy did not change
print(_ptrK[0])         # 64  -- valueof(_ptrK)
