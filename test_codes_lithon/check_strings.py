# check_strings.py
#
# Strings are NOT supported yet: the frontend lowers a bare string literal to
# "expression node Constant not supported yet". Kept here as the pinned
# example of that gap -- running it fails loudly rather than silently
# producing a wrong answer.
#
#   $ lithon test_codes_lithon/check_strings.py
#   NotImplementedError: expression node Constant not supported yet
#
# Until that lands, the closest working equivalent is `len()` on a pointer or
# list, which is capacity-driven rather than string-driven.
i: int[64] = 64
_p: ptr[int[8]] = addressof(i)
print(_p == 0)