# Lithon Error Calibration System

A companion document to `lithon_roadmap.md`. Where the roadmap sequences
*features*, this document specifies one cross-cutting system every feature
in every phase must route its failure modes through: the tier a fallible
operation belongs to, and what that tier obligates the compiler to do.

**Current implementation status, stated plainly up front:** parts of this
system are already shipped and tested (Tier 1's literal-range checks,
Tier 3's Div/Mod/shift/list-bounds traps, Tier 0's `_`-prefix
convention). Other parts are designed here but not yet built (`fallible[T]`,
the formal tier-determination algorithm as actual compiler code, the
error-code table, dynamic-overflow trapping for `Add`/`Sub`/`Mul`). Section
9 gives the full shipped-vs-designed breakdown. Nothing in this document
should be read as "already true of Lithon today" unless section 10 says so.

---

## 1. Introduction — the problem this solves

Lithon's stated promise is "if a type flow cannot be mathematically proven
safe, Lithon will refuse to compile it." That promise has a silent gap in
it today, and `find_e.py`'s factorial bug is the proof: `get_factorial`
multiplies a runtime-valued `int[64]` repeatedly, the true result outgrows
64 bits, the compiler's static range check correctly determines it cannot
prove this is safe — and then does *nothing further*. No error, no trap,
no refusal. The value silently wraps, at `lim=66` lands on exactly `0`
(because `66!` happens to be divisible by `2^64`), and the actual failure
surfaces three lines later as a confusing division-by-zero, nowhere near
its real cause.

This is not a bug in one function. It's a **structural incompleteness**:
Lithon has excellent handling for the operations someone has already
*thought to* add a check for (`Div`, `Mod`, `Shl`/`Shr`, literal-overflow),
and an undefined, un-tiered fallthrough for everything else. A new
operation added without someone separately remembering to wire up its
failure handling inherits this hole by default.

**The fix is not "add more checks."** It's making every fallible
operation's fate a required, compiler-verified classification — so that
forgetting to classify something is itself a compile error, not a silent
gap waiting to be found by a confused user three function calls later.

---

## 2. Core principle: provability as the organizing axis

Every other language's error system answers *"what do I do when this
fails?"* Lithon's question comes first: ***"does this operation's failure
mode admit a mathematical proof, and if not, who is responsible for the
failure — the environment, or the program?"*** The answer to that question
determines everything else: whether a check exists at all, whether it
costs anything at runtime, and whether the program can recover or must
stop.

### What each prior-art language actually teaches (not what to copy)

| Language | The one lesson worth keeping |
|---|---|
| C | Never let fallibility be invisible or optional-by-convention (`errno`'s failure mode) |
| C++ | One coherent mechanism beats two competing ones (exceptions vs. codes, inconsistently applied) |
| Rust | Structurally distinguish "the world said no" from "the program is wrong" — `Result` vs. `panic!` |
| Zig | Fallibility belongs in the type signature, not discoverable only by reading the body; dangerous-but-sometimes-wanted behavior is opt-in, never a silent default |

Lithon takes the *lesson*, not the mechanism. It does not get Rust's
`Result<T,E>` sum type or Zig's `!T` error-union verbatim — it derives the
same discipline from machinery it already has (RCR's proof engine) rather
than importing a type-system feature wholesale.

---

## 3. The four tiers

### Tier 0 — Unproven (the `_` world)

**What it is:** operations where Lithon makes no safety claim whatsoever,
by design, because the claim is not provable in general. Pointer
dereference (`valueof`), pointer arithmetic, raw syscalls — everything
already designed in Phase 4.4/5.1 of the roadmap.

**The rule that must hold:** the set of operations that can **produce** a
Tier-0 value must be a small, fixed, enumerable list — `addressof`,
`valueof`, a syscall's raw return — never an open set. This is what makes
the `_`-prefix convention meaningful: if *anything* could silently produce
an unproven value, the prefix would stop being a reliable signal. The
boundary functions are the only crossing points, same as already decided.

**Compiler obligation:** enforce that a `_`-prefixed value's type is
correctly `ptr[T]` (or a Tier-0-marked syscall result), and that it is
never implicitly used where a non-`_` value is expected. Nothing more —
by definition, Lithon attempts no proof inside Tier 0.

### Tier 1 — Proven impossible (static elimination)

**What it is:** RCR's static analysis has *proven*, not merely checked,
that the failure cannot occur. Today: literal-constant range checks
(`int[8] x = 100; x = x+50`), literal-zero shift/divisor detection.
Later, per the roadmap: SCEV-based induction-variable reasoning extends
this to more cases.

**The defining property:** this is the *only* tier where emitting zero
runtime instructions is the *correct* implementation, not a shortcut.
Every other tier requires an active decision about what check to run;
Tier 1 requires an active decision about what to *prove*, and once proven,
nothing further is owed.

**Compiler obligation — the ordering rule:** for every fallible operation,
**attempt Tier-1 proof first, unconditionally, before considering any
other tier.** An operation only falls through to Tier 2 or Tier 3 when
the proof attempt genuinely fails — it must never be skipped as an
optimization or convenience. This is what makes the tier system a
*completeness* guarantee rather than a lucky sequence of individually
remembered checks.

### Tier 2 — Environmental (the world's fault, recoverable)

**What it is:** a failure a *correct* program can legitimately encounter,
caused by something outside the program's own logic — a file that doesn't
exist, a connection that's refused. Per the existing syscall design: raw,
unchecked `-errno`-style returns, because trapping on a legitimate external
outcome would be wrong — the program should be allowed to handle it.

**The representation — a new, minimal primitive, not a full sum type:**
building Rust-style tagged unions is a large, separate type-system
project with no other current user. Instead:

```
fallible[T]   -- a built-in 2-field struct: { ok: bool, value: T }
```

This reuses the `struct` machinery from roadmap 4.6 directly — `fallible`
needs no new IR op, no new codegen path beyond what struct already
requires. A function that can fail returns `fallible[T]`, visibly, in its
signature:

```python
def read_file(_fd: ptr[int[64]]) -> fallible[int[64]]:
    ...
```

**Compiler obligation — use-before-check is a compile error.** Reading
`.value` on a `fallible[T]` without having checked `.ok` on that same
value, on every path that reaches the read, is a new RCR error — the same
shape of check as an existing use-before-assign rule, extended to a new
case. This is Lithon's version of Rust's `#[must_use]` / C++'s
`[[nodiscard]]`, achieved through the same provability lens as everything
else, not bolted on as an attribute.

```python
result: fallible[int[64]] = read_file(_fd)
if result.ok:
    value: int[64] = result.value   # fine -- .ok was checked on this path
```

**The two-form rule.** A Tier-2 operation always has a *fallible primary
form* (`try_int(input(...))` returns `fallible[int[N]]`). It *may* also offer
a **trap-on-failure convenience form** under a different name
(`int(input(...))` returns a plain value and terminates through a Tier-3 trap
on failure) — the same split as Rust's `Result` versus `.unwrap()`. The names
must differ, so adding the fallible form to an operation that shipped
trap-only never changes what existing programs mean. A syscall's raw
`-errno` return is a separate, `_`-gated convention (Tier 0) and is not part
of this rule.

### Tier 3 — Program fault (unrecoverable, runtime trap)

**What it is:** a failure that indicates the *program's own logic* is
wrong at this point in its execution — not the environment's fault.
Already shipped: `Div`/`Mod` zero-check, `Shl`/`Shr` bounds check, `list`
index bounds. (A stack-overflow guard is designed but **not built** — see
section 9.) All route through one shared trap mechanism
(`host_report_error` / the `emit_host_error_trap` lambda already in
`compile_function.h`) that prints a message and terminates — no recovery
path is offered, by design, because a program fault means the program's
remaining state cannot be trusted.

**New obligation this document adds — structured error codes.** Today's
trap messages are individually well-written prose
(`"RCR error: division by zero"`) but not machine-matchable. Every Tier-3
trap and every Tier-1 RCR rejection should carry a stable, numbered code
alongside its prose, the same spirit as the existing `V1_SPEC 0.6.5`-style
citations, formalized:

```
LITHON-E0301: division by zero
LITHON-E0302: shift amount out of range
LITHON-E0303: integer overflow (dynamic operand, Tier-1 proof failed)   <- new, closes the find_e.py gap
LITHON-E0401: stack overflow
```

This makes errors greppable, testable by code rather than prose-matching,
and gives `docs/whitepaper.md` — currently empty — its first real content:
a table of every error code, its tier, and its trigger condition.

---

### 3a. Decision: dynamic integer overflow (`E0303`)

**Trap by default; wrap only by explicit request.** A dynamic
`Add`/`Sub`/`Mul` whose result the compiler cannot prove in range is a
Tier-3 trap (`E0303`), identically in both tiers.

Why this, and not "wraparound is the defined behavior":
- It is the one place the type system's guarantee currently stops. Narrow
  ints are statically sound — RCR proves every `int[8]`/`[16]`/`[32]` result
  fits — and `int[64]` is exempt only because there is no wider type to
  widen into. Trapping extends the existing guarantee to the one case it
  skips; it does not introduce a second rule.
- Silent wraparound is the failure no test can see: both tiers wrap
  identically, so the differential suite stays green while the answer is
  wrong (`fact(66) == 0`).
- This document's own rule: wraparound is never the silent outcome of an
  unproven operation. It is allowed when proven exact, or when requested.

**The opt-out is a function call, not an operator.** Code that wants
wraparound (hashes, PRNGs, checksums) writes `wrap_add(a, b)`,
`wrap_sub(a, b)`, `wrap_mul(a, b)`. Zig's `+%` is not available: the frontend
parses through CPython's `ast`, which rejects an unknown operator before
Lithon sees it (the same reason a struct is `class X(Struct)`, not a new
keyword). Like `addressof`/`valueof`, these are compiler-recognized names.
v1 covers `int[64]` operands only.

**Mechanism.** The hardware already reports it: `add`, `sub` and `imul` set
the overflow flag on signed overflow, so the check is a single `jo` to the
shared trap — no extra compare. The interpreter uses the
`__builtin_*_overflow` family, so both tiers trap on exactly the same
operations.

**Costs and interactions, stated up front:**
- Every unproven `int[64]` add/sub/mul gains one predictable, not-taken
  branch. Measure with `native_bench.py --compare` before and after, and
  elide the check whenever RCR proves the range.
- Compiler-internal arithmetic (dict hashing, address computation, pointer
  arithmetic) is exempt: it is not user `int[64]` arithmetic, and pointer
  arithmetic is Tier 0 and wraps by design.
- The SIMD reduction pass cannot trap per lane. An `int[64]` vectorized sum
  must refuse unless the range is proven, or check the accumulated result —
  decide this before touching the pass (see the `int[32]` overflow note in
  `compile_function.h`, near line 545, for the stance the pass already takes).

## 4. The tier-determination algorithm

This is the actual mechanism, run by the compiler for **every** IR
instruction with a possible failure mode, with no exceptions:

```
for each instruction I with a possible failure mode:

  1. Is I's operand (or I itself) Tier-0-tainted
     (touches a `_`-prefixed value, or is addressof/valueof/syscall)?
       -> Tier 0. No proof attempted. Stop.

  2. Attempt Tier-1 static proof (RCR's range/width/capacity/
     shift-bounds analysis; SCEV once it lands).
       -> Proof succeeds: Tier 1. Zero runtime check emitted. Stop.

  3. Is I's failure mode classified "environmental" by deliberate
     language design (today: syscalls only -- this list grows only
     by an explicit decision, never by inference)?
       -> Tier 2. Must return fallible[T]. Use-before-check enforced.
          Stop.

  4. Default: Tier 3. Emit a runtime check + a call into the shared
     trap, with a numbered error code. Stop.
```

**Step 4 is the rule that closes the `find_e.py` gap.** Dynamic integer
overflow on `Add`/`Sub`/`Mul` currently has no tier at all — step 2 fails
and nothing happens next. Under this algorithm, it falls through to step
4 by construction: every fallible operation lands *somewhere*, because
the algorithm has no path that produces "nothing."

**A fifth, distinct case worth naming so it isn't confused with Tier 1:**
some operations have no failure mode *at all* — `Not` on a bool, an `Add`
on two values RCR has already proven are in range from an earlier Tier-1
pass. This isn't "Tier 1 because proven safe this time" — it's "not a
fallible operation, full stop," and it never enters the algorithm above.
Conflating "proven safe" with "cannot fail" is a real mistake to guard
against when writing the per-opcode classification table (section 6).

---

## 4a. The complete opcode-to-tier mapping

This is what makes the algorithm concrete rather than aspirational — every
IR op that can fail, resolved to its tier, with the specific condition
under which it falls through from one tier to the next. "Falls through
when" names exactly what makes Tier-1 proof fail for that op; there is no
op below with an unlisted or undefined fourth case.

| Op | Tier 1 condition (proof succeeds) | Falls through to | Tier 2/3 behavior |
|---|---|---|---|
| `Add`, `Sub`, `Mul` (const operands, known width) | Full-range arithmetic stays within declared width | Dynamic operand → **Tier 3** | Runtime overflow check + trap (`E0303`) — **not yet built**, the `find_e.py` gap |
| `Div`, `Mod` | Literal non-zero divisor | Dynamic divisor → **Tier 3** | Runtime zero-check + trap (`E0301`/`E0302`) — **shipped** |
| `Shl`, `Shr` | Literal shift amount, `0 <= amount < width` | Dynamic amount → **Tier 3** | Runtime bounds check + trap (`E0304`) — **shipped** |
| `IndexStore` (list/dict, capacity check) | Constant-provable index/length vs. declared capacity | Dynamic index/length → **Tier 3** | Runtime bounds check + trap (`E0305`) — **shipped for list**, dict TBD |
| Stack depth (every function prologue) | N/A — always dynamic, no static proof form exists | Always **Tier 3** | Runtime depth check + trap (`E0401`) — **not built**: unbounded recursion is a raw `SIGSEGV` in both tiers (verified) |
| `addressof`, `valueof`, syscall raw op | N/A — Tier 0 by definition, proof never attempted | **Tier 0** | No check. `_`-prefix required on the declaring identifier — **shipped (addressof/valueof design); syscalls designed, not built** |
| A syscall's return value | N/A — environmental by deliberate classification | **Tier 2** | `fallible[T]` return, use-before-check enforced — **designed, not built** |
| `Not`, comparisons (`Lt`/`Gt`/`Eq`), `And`/`Or` | N/A — these ops cannot fail; not part of the tier system at all (see the "fifth case" in section 4) | — | No check of any kind, ever |

## 4b. Exact syntax per tier

**Tier 0** — the `_`-prefix, already in the type itself:
```python
_p: ptr[int[64]] = addressof(x)
v: int[64] = valueof(_p)
```

**Tier 1** — no syntax at all, by design. Ordinary-looking code that the
compiler either accepts silently (proof succeeded) or refuses at compile
time with a cited error code:
```python
x: int[8] = 100
x = x + 50   # LITHON-E0101: compile-time refusal, not a runtime event
```

**Tier 2** — `fallible[T]` as the return type, `.ok`/`.value` as ordinary
struct field access (no new grammar, per the decision in section 3):
```python
result: fallible[int[64]] = read_file(_fd)
if result.ok:
    value: int[64] = result.value
# reading result.value here, outside the if, is a compile-time
# use-before-check error -- LITHON-E0201
```

**Tier 3** — also no special syntax; the operation looks ordinary, the
trap is implicit and unconditional:
```python
a: int[64] = get_runtime_value()
b: int[64] = get_another_runtime_value()
c: int[64] = a / b   # traps at runtime (E0301) iff b == 0; otherwise silent, free
```

## 4c. The error code table (proposed numbering scheme)

Grouped by tier, as settled in the open-questions section — the first
digit after `E` names the tier, making "what kind of failure is this"
readable from the code alone.

| Code | Tier | Trigger |
|---|---|---|
| `E01xx` | 1 (static refusal) | `E0101` overflow-on-reassignment, `E0102` literal zero divisor, `E0103` literal shift out of range, `E0104` literal-provable capacity overflow, `E0105` unsupported type width |
| `E02xx` | 2 (environmental) | `E0201` fallible value read before `.ok` check |
| `E03xx` | 3 (dynamic runtime trap, arithmetic/logic) | `E0301` division by zero, `E0302` modulo by zero, `E0303` dynamic integer overflow, `E0304` shift amount out of range, `E0305` dynamic index/capacity out of range, `E0306` invalid integer literal in input, `E0307` input value outside the target width, `E0308` end of input |
| `E04xx` | 3 (dynamic runtime trap, resource) | `E0401` stack overflow |

This table is the seed content for `docs/whitepaper.md`, which is
currently empty — every code above should eventually have a one-paragraph
entry there, not just the row in this table.

## 5. Tier interop — tiers are per-operation, not contagious

A `fallible[T]`'s `.value`, once read past a passing `.ok` check, becomes
an **ordinary value** — it re-enters the tier-determination algorithm from
step 1 for whatever happens to it next, exactly like any other
runtime-computed value. Tier-2 status does not "infect" downstream
computation. This matters because it keeps the system compositional: you
never need to ask "is this int secretly still Tier 2 three operations
later" — the answer is always no, by construction.

---

## 6. Validation — matching the project's own established discipline

- **A per-opcode tier classification test** (`error_tier_test.cpp`):
  for every IR op with a possible failure mode, assert which tier fires
  under each representative input shape (compile-time-constant operand,
  runtime-dynamic operand, Tier-0-tainted operand). This is the test that
  would have caught the `find_e.py` gap before a user did.
- **A mutation test**, same discipline as the Phi-resolution mutation test
  (25/25 failures when deliberately broken): pick one Tier-3 check,
  disable it, confirm some existing test in the suite fails. If nothing
  fails, that's a coverage gap in the *test suite*, not a correct
  "nothing to see here."
- **`tools/check_error_codes.py`**: scan every emitted error string in the
  source, confirm each one carries a valid, unique, documented code;
  fail the build if a new error string appears with no code, or two
  strings share a code.
- **Fuzzing**: extend `fuzz_diff.py` to generate programs with
  dynamic-valued arithmetic specifically designed to overflow (large
  runtime-computed multiplicands, as in `find_e.py`) and confirm the
  interpreter and JIT agree on *which tier fired*, not just on the final
  output value.

---

## 7. Worked examples, mapped against the algorithm

| Case | Step 2 (static proof) | Lands in | Today's actual behavior |
|---|---|---|---|
| `int[8] x=100; x=x+50` | Succeeds (full-range arithmetic proves overflow) | **Tier 1 refusal** (compile-time RCR error) | Already correct |
| `5 / 0` (literal) | Succeeds (literal zero detected) | **Tier 1 refusal** | Already correct |
| `a / b`, `b` runtime-valued, possibly `0` | Fails (not a compile-time constant) | **Tier 3** (runtime trap) | Already correct |
| `fact = fact * lim` in `get_factorial`, `lim` runtime-valued | Fails | **Should be Tier 3** (runtime overflow check + trap) | **Currently nothing — the gap this document closes** |
| `read_file(_fd)` returns nonzero | N/A — Tier 0 operation | **Tier 2** (`fallible[int[64]]`, caller must check `.ok`) | Design decided, not yet built |
| `valueof(_p)` where `_p` is dangling | N/A | **Tier 0** — no claim made, by design | Already correct, documented as a known gap |

---

## 8. Where this sits in the roadmap

This document is a cross-cutting addition, not a new numbered phase in the
build-order sense — every phase from here forward should route its
fallible operations through section 4's algorithm rather than inventing
ad hoc handling per feature. Concretely:

- **Phase 4.1 (`list`)**: a dynamic out-of-range index is Tier 3 (runtime
  trap) — already decided (`list index out of range trap`), now formally
  slotted into this system rather than a one-off decision.
- **Phase 4.3 (`dict`)**: a full-table dynamic insert is Tier 3 — already
  decided, same slot.
- **Phase 4.4 (pointers)**: Tier 0, as designed.
- **Phase 5.1 (syscalls)**: Tier 2, via `fallible[T]` — this document is
  what makes that design concrete and buildable rather than a stated
  preference with no representation.
- **Retroactive fix, independent of any future phase:** dynamic integer
  overflow on `Add`/`Sub`/`Mul` needs a Tier-3 runtime check added now —
  this isn't gated on anything else in the roadmap, and it's the one item
  here with a known live bug (`find_e.py`) demonstrating the gap.

---

## 9. Shipped vs. designed — the honest inventory

**Already shipped, tested, real today:**
- Tier 1 literal-range overflow checks (`int[8]` reassignment example)
- Tier 1 literal-zero detection for `Div`/`Mod`
- Tier 3 runtime traps for `Div`/`Mod` zero-divisor, `Shl`/`Shr` bounds,
  `list` index/capacity
- The shared trap mechanism itself (`host_report_error` /
  `emit_host_error_trap`) that every Tier-3 check already routes through
- Tier 0's `_`-prefix convention and the `addressof`/`valueof` boundary

**Designed in this document, not yet built:**
- `fallible[T]` as a type (blocked on `struct`, roadmap 4.6)
- The use-before-check typecheck rule for `fallible[T]`
- The tier-determination algorithm as an explicit, shared piece of
  compiler code — today, each op's handling is written independently, so
  there is no single place that *guarantees* every op lands somewhere
- Dynamic-overflow trapping for `Add`/`Sub`/`Mul` (`E0303`) — the fix for
  the live `find_e.py` gap, and the single highest-priority item in this
  whole document, since it's the one piece with a demonstrated real bug
  behind it rather than a hypothetical gap
- The full numbered error-code table (today's error strings are prose
  only, no codes)
- `dict` capacity trapping (list's equivalent exists; dict's doesn't yet)
- `error_tier_test.cpp`, `tools/check_error_codes.py`, and the
  overflow-specific fuzz mode
- A **stack-depth guard** (`E0401`). An earlier revision of this document
  listed it as shipped; that was wrong, and was never checked against the
  source. Verified against the repo: no guard exists, and unbounded
  recursion is a raw `SIGSEGV` (exit 139) in both the interpreter and the
  native tier.

**Verified open as of the last repo check** (each reproduced, not assumed):
- Dynamic `int[64]` arithmetic wraps silently in both tiers
  (`fact(25) = 7034535277573963776`, `fact(66) = 0`,
  `INT64_MAX + INT64_MAX = -2`). Narrow ints are statically sound — RCR proves
  every `int[8]`/`[16]`/`[32]` result fits — but `int[64]` has no wider type
  to widen into, so it is exempt. The README does not document this.
- Unsupported widths are accepted: `int[7]`, `int[128]`, `float[128]`.
  `int[128]` is treated as 64-bit, so doubling `x: int[128]` a hundred times
  prints `0` instead of `2^100`, in both tiers.

**What this means practically:** if asked "does Lithon have an error
system," the honest answer is "yes, a real and tested one for the cases
someone has individually built — Div, Mod, shifts, list bounds — and this
document is what turns that into a *closed, guaranteed*
system rather than a growing list of individually-remembered cases."

## 10. Open questions, not yet decided

- Should `fallible[T]`'s `ok`/`value` fields be accessed via `struct`-style
  dot syntax exactly, or does Tier 2 want its own dedicated `try`/`catch`-
  style syntax sugar on top (readability vs. "don't invent new grammar
  when struct access already does the job")?
- ~~Does the dynamic-overflow check apply to every `Add`/`Sub`/`Mul`?~~
  **Resolved (section 3a):** yes, every unproven `int[64]` operation, with
  `wrap_add`/`wrap_sub`/`wrap_mul` as the explicit opt-out.
- Error code numbering scheme: sequential by tier (`E01xx`=Tier1,
  `E03xx`=Tier3, etc., as sketched above) or grouped by feature area? The
  tier-grouped scheme makes "what tier is this" readable from the code
  alone, which fits this document's whole thesis — worth defaulting to
  that unless a reason emerges not to.
