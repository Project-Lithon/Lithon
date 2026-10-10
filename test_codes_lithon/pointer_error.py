# pointer_error.py
#
# The pointer `_` rule (4.4) enforced by the frontend: a pointer binding must
# carry a leading underscore, because raw memory has to be visible in the
# source. The two names below are deliberately wrong, so this file exists to
# fail:
#
#   $ lithon test_codes_lithon/pointer_error.py
#   NotImplementedError: a pointer is named with a leading underscore,
#   write `_ptrI: ptr[...]` or pick a name that starts with '_' (4.4)
#
# The corrected spelling is in check_pointers.py.
i: int[16] = 22
ptrI: ptr[int[16]] = addressof(i)
print(valueof(ptrI))