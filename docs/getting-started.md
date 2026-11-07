# Getting started (Lithon 4.10)

1. Build the engine (see README Quickstart).
2. Run a first `.lio`:

```lio
x: int[64] = 10
print(x)
```

3. Run a class end to end (`tests/typed_regression/class_basic.lio`): construct → method → inherit → `super()` → print — see `docs/classes.md`.
4. Fuzz classes: `python3 tools/fuzz_diff.py --count 200 --oop`.

Pointer gotcha: a variable whose name starts with `_` is a raw-memory name (see README). Source files are `.lio`, not `.py`.