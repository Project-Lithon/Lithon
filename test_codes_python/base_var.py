"""Plain-CPython equivalent of test_codes_lithon/base_var.py.

Same program: declare a binding with a type and no value, assign it, print.
CPython has no annotation-only declaration, so the annotation is dropped and
the binding is simply assigned -- the definite-assignment rule is Lithon's.
"""
i = 0
print(i)