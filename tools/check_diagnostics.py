#!/usr/bin/env python3
"""The error display: every refusal, syntax error and checker error is rendered with a
code, the message, `file:line:col`, the source line and a caret under the right columns.

Each case compiles a small program through the real `lithon` library entry point and
checks the rendered report: the original LITHON-Exxxx text is intact (tools match on it),
the location is the one the offending construct really has, and the caret underlines
exactly the expected source text. Also checks colour rendering and that an import error
points into the imported file.
"""
import pathlib, re, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import init as lithon  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")
    fails += not ok


def compile_text(text, include=()):
    d = tempfile.mkdtemp()
    p = pathlib.Path(d) / "prog.lio"
    p.write_text(text)
    try:
        lithon.compile(str(p), include=include)
    except lithon.LithonError as e:
        return e, p
    return None, p


def underlined(report):
    """The text under the caret line: (line number, source line, underlined text)."""
    lines = report.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(\d+) \| (.*)$", ln)
        if m and i + 1 < len(lines):
            caret = re.match(r"^\s*\| ( *)(\^+)", lines[i + 1])
            if caret:
                start, width = len(caret.group(1)), len(caret.group(2))
                return int(m.group(1)), m.group(2), m.group(2)[start:start + width]
    return None


def case(name, text, code, line, under, include=()):
    err, _ = compile_text(text, include)
    if err is None:
        check(name, False, "compiled without error")
        return
    rep = str(err)
    loc = underlined(rep)
    ok = (code in rep and loc is not None and loc[0] == line and loc[2] == under
          and re.search(r"--> .*prog\.lio:%d:\d+" % line, rep) is not None)
    check(name, ok, f"{rep!r} / underlined={loc}")


print("compile-time refusals carry the right position:")
case("a str-returning call used as a scalar", 'def f() -> str[4]:\n    return "a"\n\nx: int[64] = f()\n',
     "LITHON-E0114", 4, "f()")
case("a literal that does not fit", 's: str[2] = "abc"\nprint(s)\n', "LITHON-E0111", 1, 's: str[2] = "abc"')
case("a string literal used as a number", 'x: int[64] = "no"\n', "LITHON-E0114", 1, '"no"')
case("a list comprehension", "y: int[64] = [i for i in range(3)]\n", "LITHON-E0114", 1, "[i for i in range(3)]")
case("an error deep inside a function body",
     'def g(n: int[64]) -> int[64]:\n    t: int[64] = 0\n    t = "x"\n    return t\n\nprint(g(1))\n',
     "LITHON-E0114", 3, '"x"')
case("a const line (the keyword is stripped before parsing: columns must still line up)",
     "const N: int[64] = \"z\"\n", "LITHON-E0114", 1, '"z"')
case("a tab-indented line", 'def g() -> int[64]:\n\treturn "x"\n\nprint(g())\n', "LITHON-E0114", 2, '"x"')

print("syntax errors:")
err, _ = compile_text("x: int[64] = (\n")
rep = str(err)
check("an unclosed bracket is a located syntax error, not a traceback",
      "error[syntax]" in rep and "never closed" in rep and "prog.lio:1:" in rep and "^" in rep, rep)
err, _ = compile_text("def f(:\n    pass\n")
check("a parser syntax error is located", err is not None and "error[syntax]" in str(err) and "^" in str(err), str(err))

print("imports:")
d = pathlib.Path(tempfile.mkdtemp())
(d / "badmod.lio").write_text('def helper() -> int[64]:\n    return "no"\n')
(d / "main.lio").write_text("from badmod import helper\nprint(helper())\n")
try:
    lithon.compile(str(d / "main.lio"))
    check("an error inside an imported module", False, "compiled")
except lithon.LithonError as e:
    rep = str(e)
    check("an error inside an imported module points into that file",
          "badmod.lio:2:" in rep and underlined(rep) is not None and underlined(rep)[2] == '"no"', rep)

print("checker and runtime errors:")
d2 = pathlib.Path(tempfile.mkdtemp())
(d2 / "t.lio").write_text("def f(a: int[64]) -> int[32]:\n    c: int[32] = a + 1\n    return c\n\nprint(f(3))\n")
try:
    lithon.run(str(d2 / "t.lio"), mode="interp")
    check("a type error", False, "ran")
except lithon.LithonError as e:
    rep = str(e)
    u = underlined(rep)
    check("a type-checker error is positioned at its exact line (from the IR, not a name lookup)",
          "error[type]" in rep and "t.lio:2" in rep and u is not None and u[0] == 2 and u[2] == "c: int[32] = a + 1", rep)
    check("several checker messages about one statement share one snippet",
          rep.count("c: int[32] = a + 1") == 1 and "(as above)" in rep, rep)
    check("the checker's overflow range is a real range, not wrapped garbage",
          "-9223372036854775807..-9223372036854775808" not in rep, rep)
(d2 / "o.lio").write_text("a: int[64] = 9223372036854775807\nb: int[64] = a + 1\nprint(b)\n")
try:
    lithon.run(str(d2 / "o.lio"), mode="interp")
    check("a runtime trap", False, "ran")
except lithon.LithonError as e:
    rep = str(e)
    u = underlined(rep)
    check("a runtime trap keeps its code and text and gets help",
          "runtime error[LITHON-E0303]" in rep and "integer overflow in add" in rep and "wrap_add" in rep, rep)
    check("a runtime trap shows the source line it happened on",
          "o.lio:2" in rep and u is not None and u[0] == 2 and u[2] == "b: int[64] = a + 1", rep)

print("runtime traps at the right line, in both tiers:")
d3 = pathlib.Path(tempfile.mkdtemp())
progs = {
    "div": ("a: int[64] = 5\nz: int[64] = 0\nprint(a)\nprint(a % z)\n", 4, "print(a % z)", "modulo by zero"),
    "idx": ("xs: list[int[64], 3] = [1, 2, 3]\ni: int[64] = 7\nprint(xs[i])\n", 3, "print(xs[i])", "list index out of range"),
    "fn": ("def boom(x: int[64]) -> int[64]:\n    y: int[64] = 9223372036854775807\n    return y + x\n\nprint(boom(1))\n",
           3, "return y + x", "integer overflow in add"),
    "str": ("def f(s: str[4]) -> int[64]:\n    z: int[64] = 0\n    return len(s) % z\n\nprint(f(\"ab\"))\n",
            3, "return len(s) % z", "modulo by zero"),
}
for name, (text, line, under, msg) in progs.items():
    (d3 / f"{name}.lio").write_text(text)
    for mode in ("interp", "strict"):
        try:
            lithon.run(str(d3 / f"{name}.lio"), mode=mode)
            check(f"{name} ({mode})", False, "ran")
        except lithon.LithonError as e:
            rep = str(e)
            u = underlined(rep)
            check(f"{name}: {msg} points at line {line} ({mode})",
                  msg in rep and f"{name}.lio:{line}" in rep and u is not None and u[2] == under, rep)

print("colour:")
err, _ = compile_text('x: int[64] = "no"\n')
colored = err.render(True)
check("colour is ANSI and only on request", "\033[" in colored and "\033[" not in str(err), repr(colored[:80]))
check("the plain and coloured reports carry the same text",
      re.sub(r"\033\[[0-9;]*m", "", colored) == str(err))

print("\nall diagnostics checks passed" if not fails else f"\n{fails} diagnostics check(s) FAILED")
sys.exit(1 if fails else 0)
