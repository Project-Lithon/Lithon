"""Plain-CPython equivalent of test_codes_lithon/Pointers/pointer_error.lio.

NOTE: in Lithon this file is a *pinned failure*. The engine refuses it on
purpose, so the naming rule stays visible:

    $ lithon test_codes_lithon/Pointers/pointer_error.lio
    LITHON-E0122: a pointer is named with a leading underscore,
    write `_ptrI: ptr[...]` or pick a name that starts with '_' (4.4)

The corrected spelling is in Pointers/check_pointers.lio. CPython has no
pointer naming rule, so this file runs the corrected shape here to keep the
pair side by side.
"""
i = 22
_ptrI = [i]
print(_ptrI[0])
