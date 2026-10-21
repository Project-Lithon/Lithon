"""Plain-CPython equivalent of test_codes_lithon/DataTypes/check_strings.lio.

Same program: the closest working equivalent to a string operation in
Lithon -- `len()` on a container, which is capacity-driven rather than
content-driven, because a bare string literal is not yet a value in Lithon.

NOTE: in Lithon this file is a *pinned failure*. The engine refuses it
on purpose so the gap stays visible:

    $ lithon test_codes_lithon/DataTypes/check_strings.lio
    LITHON-E0114: expression node Constant not supported yet

The corrected spelling (same pointer, same pointee width) does run, and
prints False -- a real address is never null.
"""
xs = [0, 0, 0, 0, 0, 0, 0, 0]
print(len(xs))   # 8
