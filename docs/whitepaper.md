# Lithon Whitepaper — v1.0.0 (2026-11-05)

Design record for Lithon. §4.10 documents the class/object stages (4.10.0–4.10.6) with per-stage verification snapshots. Error-code calibration lives in `docs/lithon_error_system.md`; the error-code table below is the shared source — every row matches `tools/check_error_codes.py` and `docs/lithon_error_system.md`. Cumulative verification at 4.10.6 close: typed 61/61, tier-diff 235/235 (native 134), error-codes 176, ctest 107, `--oop` fuzz 200/200.

## Error code table

Grouped by tier — the first digit after `E` names the tier, making "what
kind of failure is this" readable from the code alone.

| Code | Tier | Trigger |
|---|---|---|
| `E0101` | 1 (static refusal) | Overflow on reassignment |
| `E0102` | 1 (static refusal) | Literal zero divisor |
| `E0103` | 1 (static refusal) | Literal shift out of range |
| `E0104` | 1 (static refusal) | Literal-provable capacity overflow |
| `E0105` | 1 (static refusal) | Unsupported type width |
| `E0106` | 1 (static refusal) | int[64] to float[64] through `float(64, x)` (lossy: 53 bits cannot hold every int[64]) |
| `E0107` | 1 (static refusal) | `range(a,b,0)` with a literal zero step |
| `E0108` | 1 (static refusal) | Unsupported type-annotation form |
| `E0109` | 1 (static refusal) | Unknown or badly-sized annotation type name |
| `E0110` | 1 (static refusal) | Annotation missing a required field |
| `E0111` | 1 (static refusal) | Bad container capacity / literal count / `str[N]` capacity or literal length (including a `__str__`/`__repr__` literal longer than its declared result) |
| `E0112` | 1 (static refusal) | Container element must be a plain scalar or a class reference (4.10.3) |
| `E0113` | 1 (static refusal) | Pointer (`ptr[T]`) declaration rules (4.4) |
| `E0114` | 1 (static refusal) | Unsupported or restricted program construct (including a non-literal `str[N]` value, a whole `str` / `str` pointee used where a value is needed, or a non-`.lio` source extension) |
| `E0115` | 1 (static refusal) | Unsupported builtin call form (including `str(...)` anywhere but `str(n, input("prompt"))` as a `str[N]` initializer or assignment) |
| `E0116` | 1 (static refusal) | Builtin argument rules (`valueof`/`addressof`/`wrap_*`, and 4.12's `str(n, input(...))` limit: a non-literal `n`, `n < 1`, or `n` larger than the string's capacity) |
| `E0117` | 1 (static refusal) | Indexing / container-subscript restrictions |
| `E0118` | 1 (static refusal) | `else` clause on a `while`/`for` loop |
| `E0119` | 1 (static refusal) | `for`-loop form restrictions (including `enumerate`/`zip` over a class-typed container, and 4.11's loop-variable rules: a `range` target that is undeclared, not an `int[w]`, or narrower than its bounds can be proven to fit) |
| `E0120` | 1 (static refusal) | `break`/`continue` outside a loop |
| `E0121` | 1 (static refusal) | Assignment / target restrictions |
| `E0122` | 1 (static refusal) | Declaration and re-declaration rules |
| `E0123` | 1 (static refusal) | Tuple/dict literal shape (a tuple/list of class references must be filled by a full literal) |
| `E0124` | 1 (static refusal) | Function-header restrictions (a class-typed container may not be a parameter or return type) |
| `E0125` | 1 (static refusal) | Pointer arithmetic form |
| `E0126` | 1 (static refusal) | Type definition and hierarchy (4.6/4.7/4.10): a duplicate type name, a struct/enum body violation, an unknown/duplicate/struct/enum class base, or bases with no consistent C3 linearization |
| `E0127` | 1 (static refusal) | Field/attribute layout (4.6/4.10): a struct field or class attribute of unsupported type, a duplicate field, a class re-declaring inherited state, or multiple bases whose stateful layouts disagree at any offset |
| `E0128` | 1 (static refusal) | Struct field access (unknown field, a whole struct used where a value is needed, or a `str` field reached through a pointer into the struct) |
| `E0129` | 1 (static refusal) | Enum definition (must derive from `enum`; variants are bare unit names, payload annotations, or inline `class V(struct)`) |
| `E0130` | 1 (static refusal) | Enum variant construction (unknown variant, wrong payload shape, unknown/missing/duplicate struct-payload field) |
| `E0131` | 1 (static refusal) | `match` on an enum (bad subject/pattern, non-exhaustive, a guard, a wildcard that is not last, or an enum used where a value is needed) |
| `E0132` | 1 (static refusal) | Type-function definition (`-> newtype`): bad signature, no body or more than one statement, a body that is not a one-element `{ T }`, a duplicate name, or a name colliding with a runtime function/struct/enum |
| `E0133` | 1 (static refusal) | Type-function application: an unknown or non-call name, keyword/starred arguments, an arity mismatch, or a recursive (direct or mutual) expansion |
| `E0134` | 1 (static refusal) | Method definition (a `def` in a struct/enum/class body): a decorator other than a bare `@property`/`@classmethod`, more than one decorator, a decorated reserved/special method, `/`/`*`/`**`/defaults, a missing or annotated `self`/`cls`, an unannotated or non-scalar parameter/return, a property with arguments or no result type, a duplicate name, a name colliding with a field or variant, an unsupported class special method, a comparison dunder without one class-reference parameter and a `bool` result, or a `__str__`/`__repr__` body that is not one string-literal/scalar-f-string return |
| `E0135` | 1 (static refusal) | Method application and `super()` (4.9/4.10): an unknown method, a field where a method is called, a non-struct/enum/class receiver, keyword/starred arguments, an arity mismatch, a recursive inline, a method with no result used as a value, an incompatible inherited signature or changed method kind, an invalid `super()` form/target, a missing ordering method, an explicit `__str__`/`__repr__` call, an object with neither text method printed, a property assignment or call, a classmethod called on an instance, or an ordinary method called on a class name |
| `E0140` | 1 (static refusal) | Class instance semantics (4.10.1/4.10.2/4.10.3/4.10.4): a bare class name used where a value is needed, a class-typed variable initialized with a non-class value, an identity compare between classes with no possible common declared subclass, an attempt to instantiate an abstract class, an attribute left uninitialized on some path through `__init__`, a bare declaration or `append` on a class-typed container, storing a non-class or unrelated class reference into a class-typed container element, or membership of a non-class or unrelated class in a class-typed container |
| `E0141` | 1 (static refusal) | Class attribute access (4.10.1): a read or write of an attribute the class does not declare (an instance has exactly its declared attributes) |
| `E0142` | 1 (static refusal) | Nullable class references (4.10.7): `Optional[T]` where `T` is not a class reference or is nested, an Optional class reference in a collection, `None` outside an Optional class target (including scalar/container/index/print/augmented-assignment/enum-payload/builtin contexts), an Optional used as an object without an `is None`/`is not None` proof, or `==`/`!=` used instead of the identity checks |
| `E0143` | 1 (static refusal) | Constants (4.13): `const` with no initializer or no type, a `const` of a struct, pointer or class reference, a constant redeclared or declared over a variable, a write to a constant after its declaration (assignment, augmented assignment, an element store, `append`, a string re-read), a constant as a `for` target, taking a constant's address, or a function parameter or local that reuses a module constant's name |
| `E0144` | 1 (static refusal) | Unsigned integers (4.14): a negative or too-large literal, or a signed value, into a `uint`; a `uint` mixed with an `int` or a `float` in arithmetic or a comparison; a wider `uint` result into a narrower `uint`; a `uint` into anything but an `int[64]`; negating a `uint`; `/` of a `uint[64]`; a `uint[64]` as an index or range bound; a `uint` in a container, struct field, attribute, enum payload, method parameter, pointer, `abs`/`int`/`float`/`wrap_*`/`syscall`, or an if-expression arm |
| `E0145` | 1 (static refusal) | Modules and the entry point (4.15): an import that cannot be resolved (module not found, a name the module does not define), an import cycle, an import inside a function or block, a relative import, `from m import *`, an imported name that collides with a definition or another import, an executable top-level statement, or a constant that is not a literal scalar, in an imported program, a parameter or local that reuses an imported name, an `if __name__ == '__main__':` guard with an else branch, or `__name__` used anywhere but exactly that guard |
| `E0201` | 2 (environmental) | Fallible value read before `.ok` check |
| `E0301` | 3 (dynamic runtime trap) | Division by zero |
| `E0302` | 3 (dynamic runtime trap) | Modulo by zero |
| `E0303` | 3 (dynamic runtime trap) | Dynamic integer overflow |
| `E0304` | 3 (dynamic runtime trap) | Shift amount out of range |
| `E0305` | 3 (dynamic runtime trap) | Dynamic index/capacity out of range |
| `E0306` | 3 (dynamic runtime trap) | Invalid integer literal in input |
| `E0307` | 3 (dynamic runtime trap) | Input value outside the target width |
| `E0308` | 3 (dynamic runtime trap) | End of input |
| `E0309` | 3 (dynamic runtime trap) | float-to-int conversion out of range or not finite |
| `E0310` | 3 (dynamic runtime trap) | `range(a,b,step)` where `step == 0` (dynamic) |
| `E0311` | 3 (dynamic runtime trap) | String input (4.12): a line read by `str(n, input(...))` is longer than `n-1` bytes; it is never truncated (caught as a `ValueError`) |
| `E0312` | 3 (dynamic runtime trap) | String input (4.12): a line read by `str(n, input(...))` contains a NUL byte, which a `str[N]` cannot hold; refused rather than silently shortened (caught as a `ValueError`) |
| `E0313` | 3 (dynamic runtime trap) | Unsigned integers (4.14): a `uint[w]` operation whose exact result is outside `0..2^w-1` (a sum past the maximum, a difference below zero, a product or left shift that does not fit); never wraps (caught as an `OverflowError`) |
| `E0401` | 3 (dynamic runtime trap, resource) | Stack overflow |

## IR ops — the eight class instructions

| Op | Meaning |
|---|---|
| `New` | zeroed heap allocation + stamp `vptr`, refcount 1 (`host_class_new`) |
| `RefLoad` / `RefStore` | bare `[ref+disp]` attribute access (both tiers; per-kind: float via helper, bool 1 B, class 8 B, int zero/sign-extend) |
| `Retain` / `Release` | `IncRef` / `DecRef` (immortal sentinel makes both no-ops) |
| `IsInstance` | ancestor-bitset probe |
| `CallVirtual` | global-slot dispatch on the receiver's own descriptor |
| `CallSuper` | scan receiver's actual MRO after the defining class (`own_slot_bits`) |

`ClassInfo` carries `slot` / `ownslot` metadata (`size`/`mro`/`bases`/`abstract`); the text parser renders `class … size=/bases=/mro=/slots=` with `attr` and `slot`/`ownslot` lines. All six host-call ops are register-clobbering (`is_register_clobbering_op`), and `Kind::Object` (class refs) is print-unsafe in both tiers.

## 4.10.0 — object runtime and architecture

**`object.h` — pure layout.** An instance is `[vptr:8][refcount:8][attr0]…` (16-byte `ObjectHeader`, `static_assert(sizeof==16)`). `vptr` *is* the type tag (first word, so the JIT reads it at fixed `offsetof==0`); there is no separate `type_tag` word. Base attributes come first, so a subclass instance starts with a valid base instance and upcasting is a free pointer (no move). All attribute offsets are absolute from the object start. The legacy `value.h:ObjHeader` (`{refcount, type_tag, flags}`, 8 B) is superseded; every interpreter/JIT cast between `LithonValue.payload` and `ObjectHeader*` is an explicit `reinterpret_cast` contract.

`ClassDescriptor` fields (who reads each): `type_id` (first word, JIT fixed offset), `slot_count`/`instance_size`/`flags` (`kClassFlagAbstract`), `table_index`, `ancestor_bits`/`word_count` (`isinstance` probe), `own_slot_bits`/`word_count` (`super()` scan), `mro`/`mro_count` (C3 order), `ref_attr_count`/`ref_attr_offsets` (absolute offsets of class-typed attrs, for nested release), `name`/`destructor`/`destructor_owner`, `slots[1]` flexible tail. Allocation over-allocates `sizeof + (slots-1)*8` (`malloc`+`memset`); descriptors live for process life (emitted code carries their addresses).

**`heap.h` — allocation and refcounting.** A single 64 KiB `mmap` chunk is bump-allocated first use (no libc `malloc` on Linux; `malloc` fallback off-Linux). Freed blocks form an intrusive free list (first word = `next`) across 64 size classes (stride 16, up to 2 KiB). `reserved_bytes()` rounds the *reservation* — a 24 B request in the 32 B class hands a 32 B block (canary-tested). Bump alignment is 16; an oversized object (> chunk) gets an exact-size `mmap` block dropped on free. `g_heap.live_objects` / `destructor_runs` are the "no leak" proof (`g_live_objects==0` after a run). `reset_heap()` clears free lists and counters but *keeps* the mapping; every entry point (`run_main`, `lithon_jit.cpp:main`, `tier_runner.cpp:run_native`) calls it. **The runtime is emitted only when a `class` exists** — class-free programs pay nothing, including the AOT size budget.

**Lifetime (the four tier-parity rules, verbatim design contract).** (1) Addresses are unobservable and print-unsafe. (2) `__del__`'s moment is pinned — an optimizer may not move or elide it unless `destructor==nullptr`. (3) An exceptional path that unwinds past an owning scope leaks rather than cleaning up. (4) A tail call must release before calling. `kImmortalRefcount = 0x8000…` marks descriptors and stack temporaries; `IncRef`/`DecRef` are no-ops for them and emit no instruction. `release_drop` runs `__del__` *exactly at the last drop* (instance still fully formed, CPython `tp_dealloc` analogy), *then* releases `ref_attr_offsets` children recursively, *then* frees the block — this ordering is what prevents a `Holder.leaf = Leaf()` leak. Cycles leak by design (no tracer); deterministic refcounting gives pause-free, predictable `__del__`.

**`class_runtime.h` — the descriptor table both tiers build.** `ClassTable` mirrors IR class indices (`descriptors`/`names`/`slot_names`/`slot_owners`/`destructor_names`). It is rebuilt at *each entry point* beside `reset_heap`, so consecutive runs never see stale descriptors. The build is two-pass: pass 1 creates one descriptor per class (so MRO can name them), pass 2 fills vtables/destructors/ref-attrs — with five stable-storage pools (`descriptor_pool`, `ref_attr_pool`, `ancestor_bits_pool`, `own_slot_bits_pool`, `mro_pool`) so `c_str()` addresses survive `vector` growth. MRO comes from `ci.mro`; a legacy `base_index` walk serves hand-built IR and unit tests. Ancestor bitsets (`words=(n+1+63)/64`, bit = `type_id`) replace the legacy pre-order subtree-range test (diamonds are not contiguous). Resolvers: `__del__`+Class → destructor ptr; `(owner, slot, slot_name)` → `__virt__<Owner>.<method>` ptr; `nullptr` = inherited/abstract slot. The *interpreter* slot resolver always returns `nullptr` (it never indirect-calls a vtable; it dispatches by name: `names[owner]+"."+method` in `g_virt_functions`); the JIT resolves via `compiled.function_offset`. Host helpers are called *by address* with plain-int arguments so the JIT never agrees on a struct layout.

## 4.10.1 — classes

A baseless `class P:` lowers to heap operations, not inline storage. `Point(3,4)` emits `new Point` and splices `__init__`; a method call is inline only when the receiver's exact class is known, else `CallVirtual`. Attribute access is layout-baked `refload`/`refstore` (`[ref+disp]` both tiers, deliberately not helpers). `b=a`, `return`, and attr-store **retain**; the reference drops at the owning scope exit (block exit, loop back edge, `return`, `break`, `continue`). The callee-retains contract: a class-typed return leaves one owned reference with the caller. `__del__<Class>` is a real function run by `release_drop` at the last drop. Definite-init: every attribute assigned on every `__init__` path or construction is refused (E0140); undeclared attribute read/write is E0141. Class-typed variables are never null — there is no `None`/`Optional` yet. Tests: `class_basic.lio`, `class_lifetime.lio`, `class_references.lio`, `class_returns.lio`.

## 4.10.2 — single inheritance

Base-first layout (`CLASS_HEADER_BYTES=16`): base attributes keep their offsets, so `sh: Shape = s` (a `Square` value) is a free upcast — the pointer does not move. Method names have one global slot; `host_class_slot(obj, slot)` reads the *receiver's own* descriptor so an override runs. The JIT `CallVirtual` outgoing area is `[stack args][target][arg0][arg1]` (`argv+3`), with receiver and `arg1` (GP *or* XMM for float) saved across the resolver call; the target is fetched via the runtime helper, not `rel32`. The interpreter's `CallVirtual` is name-based (`slot_owners[table_index][slot]` → `__virt__<Owner>.<method>`). `super()` in `4.10.2` form starts after the *enclosing* class; the defining-class fix is §4.10.4. Abstract (`...`) bodies refuse construction. `is_instance_of` probes `ancestor_bits[word]` (`word=id>>6`), falling back to the legacy subtree range `lo<=type_id<=hi` for hand-built descriptors — out-of-range index returns `false`, never crashes. Tests: `class_inherit.lio`, `class_inherit_isinstance.lio`, `class_inherit_params.lio`.

## 4.10.3 — class references in containers

A `list[P, N]` or `tuple[P, N]` may hold class references. The container keeps
its existing flat-leaf representation: one 8-byte object pointer per leaf,
packed in row-major order, so a nested `list[list[P, 2], 2]` is still one
contiguous run and `xs[i][j]` is the same multi-index access a scalar container
uses. `scalar_leaf_bytes` therefore reports 8 for a class leaf, and
`container_layout` accepts a class leaf even though a class value is not a
scalar with byte-run storage of its own. Both tiers size and address the run
from that one layout.

The declared element class is the static type of every `xs[i]` read. An element
may name that class or a subclass (an upcast), so a method call through a
base-typed element dispatches on the object's real class and `isinstance` still
sees the derived class. A read is a borrow; the slot owns exactly one
reference.

A class-typed literal must fill every slot: a bare `list[P, N]` or
`tuple[P, N]` would be a run of null references and is refused, as is a shorter
literal and `append`. When an element is stored, the new reference is retained
before the old one is released, so `xs[0] = xs[0]` and `xs[1] = xs[0]` are
safe. At scope exit the flat leaf chains are walked in reverse and one
reference is dropped per slot, which makes per-element `__del__` timing
deterministic and tier-identical. A tuple builds its elements first, then
declares and fills the run in one block, because an element's inlined
`__init__` splits the IR and the type checker tracks a tuple literal per block.

`in` and `is`/`is not` compare references, with the same related-class rule as
class identity (`E0140` for unrelated classes). At the close of this stage
`==`/`!=` did too; 4.10.5 makes a direct comparison dispatch to a declared
equality method, while class-typed membership remains a reference scan.
`enumerate`/`zip` over a class-typed container are refused (`E0119`): the loop
target outlives one iteration, so its ownership is not settled; an explicit
`for i in range(N):` reading `xs[i]` is supported. A class-typed container is
local storage, not a value, so it may not be a function parameter or return
type (`E0124`). Dict class keys/values remain deferred.

Verification at the close of this stage: typed regression 57/57 (including
`class_container_list`, `class_container_inherit` and
`class_container_lifetime`), general regression 34/34, tier differential
227/227 (native 130, interpreter fallback 0), direct-Phi 91 matched / 0
mismatched, error-code checks 151, object-runtime ctest 77 ok (including the
class-container layout case), and `fuzz_diff.py --count 200 --oop` 200 passed /
0 skipped / 0 mismatches across the 4.10.1, 4.10.2 and 4.10.3 shapes.

## 4.10.4 — multiple inheritance

A class may name several earlier classes as bases: `class D(B, C):`. The
frontend computes Python's C3 linearization and emits it as `mro=` in the
module metadata, so both tiers resolve the same order. A method is found in the
first class of that MRO that declares it; `D(B, C)` and `D(C, B)` therefore
produce different lookup orders, and the guard test makes the arithmetic in the
overrides expose a wrong order. An inconsistent set of bases is refused with
`E0126` rather than silently choosing one order.

Every base keeps its attributes at their original absolute offsets. A class
packs its own attributes after the largest base block. Shared diamond fields
must agree at every offset and are stored once; two bases whose stateful
layouts collide at an offset are refused with `E0127`. That keeps an upcast to
any base free and fixed-offset access valid, at the cost of deliberately not
composing independent state from two unrelated stateful bases.

Method slots remain global by name. Each descriptor records the class that
**declares** the method at a slot (`slot <idx> <name> <owner>` in the metadata),
so a subclass that inherits a slot has no function of its own to find: both
tiers call the owner's emitted `__virt__` function. The descriptor also carries
its ancestor bitset, its own-slot bitset, and its C3 MRO; `__del__` records a
single MRO-selected owner and only that owner emits a destructor function.

Ordinary `super()` is dynamic. A `CallSuper` op packs
`(defining_index << 32) | slot`; at run time the receiver's actual MRO is
scanned after the defining class, using the own-slot bits to find the next
concrete implementation. This is what makes a diamond cooperative and lets a
method call `super().foo()` for a `foo` the enclosing class does not itself
override. The frontend requires every reachable definition to share the
signature and every concrete subclass to have a later concrete implementation,
refusing anything that could miss on some concrete receiver (`E0135`).
`super().__init__` is different: construction knows the exact class being
built, so the next constructor is resolved statically along that class's MRO
and inlined with the same definite-initialization bookkeeping. `super().__del__`
is refused because the destructor runs once at the last drop.

`isinstance(x, C)` now probes C's type id in the object descriptor's ancestor
bitset, because a diamond's descendants are not a contiguous id interval.
`issubclass` folds along C3. An identity comparison is allowed when the two
static classes have a possible common declared subclass
(`_possible_common_subclass`), not merely when one is an ancestor of the other,
since a `B`-typed and a `C`-typed reference may name the same `D`. A class-typed
result returned through `super()` or a virtual call remains exactly one owned
reference: the emitted `__virt__` return transfers its temporary, and inline,
virtual and super call results are registered so a binding claims them and a
discarded result is released at its statement.

Verification for this stage: typed regression 59/59 (including
`class_multiple_inherit` and `class_multiple_lifetime`), general regression
34/34, tier differential 231/231 (native 132, interpreter fallback 0),
direct-Phi 93 matched / 0 known gaps / 0 mismatched, error-code checks 157,
object-runtime ctest 107 ok (including the diamond MRO, owner/slot, ancestor
bitset, dynamic-super and destructor-owner cases), and
`fuzz_diff.py --count 200 --oop` 200 passed / 0 skipped / 0 mismatches across
the flat, inheritance, container and multiple-inheritance shapes.

## 4.10.5 — comparison special methods and object text

A class may declare `__eq__`, `__ne__`, `__lt__`, `__le__`, `__gt__` and
`__ge__`. They are ordinary typed methods: one parameter annotated as a class
reference (the other operand) and a `bool` result. They therefore use the
global method slots and the inline/`CallVirtual` paths already established for
class methods; a comparison site compiled against a base keeps working for a
subclass override. `a != b` uses `__ne__` when present and otherwise negates
`__eq__`. When neither equality method exists, `==`/`!=` remain pointer
identity, and `is`/`is not` are always pointer identity and never dispatch.
Ordering with no matching method on the left class is refused (`E0135`) rather
than inventing an address order.

The method is selected from the left operand's static class. Python's
rich-comparison protocol additionally gives a proper-subclass right operand a
priority turn and allows `NotImplemented` to hand control to the reflected
method; this slice deliberately omits both. `NotImplemented` is not a value in
the typed method ABI, and left dispatch keeps the comparison a normal slot call.
Class-typed membership (`x in xs`) likewise remains the 4.10.3 reference scan:
it does not become an unrolled sequence of `__eq__` calls. Direct `==`/`!=`
between two class references do dispatch. Scalar `<=`/`>=` lower to
`not (b < a)` / `not (b > a)` because there is no `le`/`ge` opcode, which is
also what lets natural `__le__`/`__ge__` bodies compare their fields.

Object text is a separate, restricted surface. `__str__` and `__repr__` must
declare a bounded `str[N]` result and contain exactly one `return` of a string
literal or an f-string whose fields are direct scalar attributes of `self`
(`int`, `float` or `bool`). They are kept in the class's method table for
resolution but are deliberately **not** vtable slots and are not callable
values: a bounded string is a run, not a register value, so there is no call
ABI that could return it. Printing an object resolves `__str__` first and
falls back to `__repr__` for the receiver's actual concrete class. When
subclasses resolve different text methods, the frontend emits a derived-first
`isinstance` chain and inlines the selected method in each branch; the text is
rendered with the existing literal/scalar print operations, so no new IR op or
runtime ABI is involved. An object with neither method is refused (`E0135`)
instead of printing an address, an explicit text-method call is refused
(`E0135`), and a literal longer than the declared `str[N]` result is refused
(`E0111`). The restricted body has no side effects, so evaluating f-string
operands first and emitting the object text afterwards preserves CPython's
observable order.

Verification at the close of this stage: typed regression 60/60 (including
`class_special_methods`), general regression 34/34, tier differential 233/233
(native 133, interpreter fallback 0), direct-Phi 94 matched / 0 known gaps / 0
mismatched, error-code checks 167, object-runtime ctest 107 ok, and
`fuzz_diff.py --count 200 --oop` 200 passed / 0 skipped / 0 mismatches across
the flat, inheritance, container, multiple-inheritance and special-methods
shapes.

## 4.10.6 — properties and classmethods

The class object is still a compile-time layout, not a runtime value. This
stage adds exactly two compiler-known decorator forms; general decorators,
stacked decorators and decorator arguments remain refused (`E0134`). The two
forms are stored as method-kind metadata (`method`, `property` or
`classmethod`) on the class method table, and an override or an inherited name
may not change that kind (`E0135`). A decorated `__init__`, `__del__` or
special method is also refused, because those names have fixed roles.

A `@property` method is a read-only computed attribute. It takes only `self`,
must declare a scalar or class-reference result, and is read without
parentheses (`p.total`). A property is inherited and overridden exactly like
an ordinary method and receives a normal global vtable slot; when a subclass
overrides it, a read through a base-typed reference emits the existing
zero-argument `CallVirtual` path and the receiver's descriptor chooses the
implementation. When the hierarchy proves there is no override, the property
body is inlined at the read site. A property has no setter or deleter:
assigning to it is refused (`E0135`), as is applying `()` to it. Its receiver
is evaluated once and handed to the inliner, so a property read preserves the
same evaluation order as a method call.

A `@classmethod` takes an unannotated first parameter named `cls` instead of
`self` and is called on a class name (`Point.origin()`). It is not an instance
method, is not called through a receiver, and deliberately has no vtable slot:
a class-level call is statically resolved and the body is inlined. Inside that
body, `cls` is a compile-time alias for the class named at the call site.
`cls(...)` constructs that class, `cls.other(...)` resolves another
classmethod on it, and `isinstance(x, cls)` / `issubclass(cls, C)` fold
statically. The alias is carried through the inline rename, so an inherited
classmethod called as `Derived.make(...)` runs the base body with `cls` bound
to `Derived` and therefore constructs a `Derived`. Calling a classmethod on an
instance, or an ordinary method on a class name, is refused (`E0135`); so is a
call to an abstract classmethod. Classmethods do not create a `__virt__`
function or a class-meta slot.

No IR opcode or runtime ABI is added. A property reuses the ordinary method
slot and `CallVirtual`/inline paths, while a classmethod is a source-level
inline with a compile-time class alias. This keeps both tiers reading the same
class metadata and preserves the closed-hierarchy assumptions of 4.10.2 and
4.10.4.

Verification at the close of this stage: typed regression 61/61 (including
`class_decorators`), general regression 34/34, tier differential 235/235
(native 134, interpreter fallback 0), direct-Phi 95 matched / 0 known gaps / 0
mismatched, error-code checks 176, object-runtime ctest 107 ok, and
`fuzz_diff.py --count 200 --oop` 200 passed / 0 skipped / 0 mismatches across
the flat, inheritance, container, multiple-inheritance, special-methods and
property/classmethod shapes. The sixth shape exercises inherited `cls(...)`
construction, `cls.other(...)`, static `issubclass(cls, C)`, property override
dispatch and a statement classmethod call.

## 4.10.7 — nullable class references

`Optional[P]` is one class reference that may name either an instance of `P`
(or a subclass) or no object at all. It is accepted as a local, a class
attribute, a parameter and a return type. The annotation is deliberately not a
general optional type: `P` must be a class reference, nested `Optional[...]`
is redundant and refused, and a collection whose element type contains an
`Optional` is deferred rather than given a second runtime representation. The
IR spelling stays `class[P]`, and nullability is a source-level obligation the
frontend enforces; there is no new IR opcode or runtime ABI.

`None` is not a general scalar zero. It is a value only for an `Optional`
class target, so a non-Optional class binding, scalar or container
declaration, index, dict key/value, print/f-string, append, conversion,
augmented assignment, enum payload, builtin or call argument refuses it
(`E0142`). This closes the otherwise
silent `None`-as-zero reading (`xs[None]` looking at `xs[0]`, `print(None)`
printing `0`, and so on). `==`/`!=` against `None` is also refused: `==` may
dispatch to a class comparison method, and `None` is not an instance of that
class. The identity forms `is None` and `is not None` are the only accepted
way to test absence.

An `Optional` value cannot be used as an object until it has been checked.
The frontend tracks a conservative fact per plain class-variable name through
`if` and `while`: `p is not None` proves `p` non-null on the true arm and
`p is None` proves it on the false arm, while an early `return`, `break` or
`continue` in one arm lets the other arm's fact survive the merge. The same
proof is discarded again by assigning `None`, and re-established by assigning
a non-null reference. Attributes do not narrow: `obj.field` may change
between the check and the use, so the programmer copies it into a local
first. A `try` handler knows only the facts from before the `try`, and a loop
exit knows only the facts from before the loop, because either path may have
run with the value replaced.

The runtime model is unchanged. A class reference is one pointer, and the
frontend represents `None` with a synthetic valueless `store name : class[P]`
followed by a `load`; both tiers initialize a valueless class store to a null
class reference, so the result is a real class-typed null rather than an
integer zero. `None` assignments, returns and arguments lower through that
null reference, and the existing retain/release sequence still owns the old
target: reassigning a nullable binding releases the reference it held at that
statement, while retain/release are null-tolerant. The SSA pipeline keeps
valueless class declarations in memory, because they reserve a slot rather
than define a value; promoting them would replace the load with an invalid
value id.

Verification at the close of this stage: typed regression 62/62 (including
`class_optional`), general regression 34/34, tier differential 237/237
(native 135, interpreter fallback 0), direct-Phi 96 matched / 0 known gaps / 0
mismatched, error-code checks 196, object-runtime ctest 107 ok, and
`fuzz_diff.py --count 200 --oop` 200 passed / 0 skipped / 0 mismatches across
the flat, inheritance, container, multiple-inheritance, special-methods,
property/classmethod and Optional/None shapes. The seventh shape exercises
nullable attributes and locals, Optional parameters and returns, a derived
object through an Optional base reference, `while` and early-return narrowing,
and the release/replace path when a nullable attribute is reassigned.

## 4.11 — declared loop variables

`for i in range(...)` assigns a variable; it does not declare one. This
restores V1_SPEC 0.6.1 and 0.6.6, which the production frontend had loosened:
it used to invent an `int[64]` counter for any undeclared name, and it stored
every counter as `int[64]` even when the program had declared a narrower one,
so a declared `float[64]` or `int[8]` target was silently retyped.

The rules are all static. The target must already be declared, and declared as
an `int[w]` (`E0119` otherwise). It must hold a value on every path before the
loop (V1_SPEC 0.6.6): the frontend emits a read of the target ahead of the
loop, and the checker's definite-assignment pass (0.6.10) refuses a declaration
that no assignment reaches, so there is a single flow analysis rather than a
second, weaker one in the frontend. The loop keeps the declared width: the
counter's initial store carries the declared `int[w]` annotation.

Width is where the checker constrains the design. Its range proof is interval
arithmetic with no branch refinement, and it exempts exactly one pattern from
range checking, the canonical `load i / const 1 / add / store i` latch. A
one- or two-argument loop ends by storing its *limit* into the counter, so a
narrower counter is sound precisely when every bound is already inside its
width. The frontend therefore accepts a narrow counter only when each bound is
a literal that fits or an integer variable declared no wider than the counter,
and refuses everything it cannot prove (`E0119`). A stepped
`range(a, b, step)` increments with an `add` that is not the exempt pattern, so
it is accepted only for an `int[64]` counter. An `int[64]` counter keeps every
existing forms unchanged. No IR opcode, checker change or runtime ABI is added.

`enumerate` and `zip` follow the same rule: the `enumerate` index is checked
exactly like a `range` target, every element target must be declared, hold a
value and have exactly the element type of its container, and only the hidden
counter that `zip` generates is exempt.

The counter now ends where CPython leaves it. A loop is lowered as a guarded,
bottom-tested loop: the counter is stored only once the loop is known to run (so
a loop that never runs leaves it untouched), and it is advanced only when
`i + 1` is still inside the range (so after `range(10)` it holds `9`, not the
limit). The test `i + 1 < limit` is computed in int64 and never stored, so it
cannot overflow a narrow counter, and the advance is still the exempt
`i = i + 1` pattern. The three-argument form already stopped on its last value;
it now also skips the initial store when it does not run. Because the counter is
the single variable the loop tests, a write to it inside the body would change
the trip count, so assigning, augmenting or re-declaring a live loop target, and
reusing it as the target of an inner loop, are refused (`E0119`).

Making loops guarded exposed two latent defects in the opt-in SSA backend, both
fixed here. (1) `plan_function` records an aliased load with the register its
variable was promoted to *before* allocation, but the allocator may later
coalesce a merge variable into a different register; stores followed the
allocator and the alias did not, so a loop-header merge read a register nothing
had written. The aliases are now re-pointed at the variable's final register
after allocation. (2) `fold_constants` turned a constant `branch` into a `jump`
without removing the dropped successor's phi operand; with direct Phis alive the
operands are positional by predecessor, so the surviving edge read the wrong
one (a guarded loop printed its initial value). The dropped edge's operand is
now removed. The loops that previously failed under `--ssa`/`--direct-phis`
(`for_range`, `nested_loop`, `break_continue`, `iter_helpers`, `if_expr`) are the
regression coverage.

Implementing it also exposed a documentation gap, closed here: the discipline
gate intersects the whitepaper table with `docs/lithon_error_system.md` and the
website reference page, and `E0142` was missing from the latter while the
reserved `E0137`/`E0138` were only named as a range.

Verification at the close of this stage: typed regression 63/63 (including
`for_declared`), general regression 34/34, tier differential 239/239 (native
136, interpreter fallback 0), direct-Phi 97 matched / 0 known gaps / 0
mismatched, error-code checks 211 (fifteen new `E0119` and definite-assignment
cases), object-runtime ctest 107 ok, every fuzz mode with no skipped programs
(`--break` was fixed to declare its counter), and the full
`tools/verify_all.sh` gate green. Five tracked programs that relied on the implicit counter
(`break_continue`, `for_range`, `nested_loop`, `range_step`,
`tail_recursion`) and one regression (`class_container_list`) now declare it.

## 4.12 — string input

`k: str[N] = str(n, input("prompt"))` reads one line from standard input into a
bounded string. It is the string twin of `int(w, input(...))` and, like it, is
the only spelling of the read.

The two numbers follow one convention. A `str[N]` holds at most `N-1` bytes
because the last byte is the terminating NUL, and the limit `n` in `str(n, ...)`
counts the NUL the same way, so the read accepts at most `n-1` bytes. `n` is a
literal (the capacity decision has to be visible in the source and checkable at
compile time) and must satisfy `1 <= n <= N`, where `N` is the capacity of the
string being read into; a non-literal `n`, `n < 1` or `n > N` is `E0116`, and any
other shape of `str(...)` is `E0115`. The form is accepted where a string is
written: a `str[N]` declaration, an assignment to a `str[N]` variable, and an
assignment to a struct's `str` field (whose companion run is the target).
Anywhere else `str(...)` has no value to give and is refused.

A line that does not fit is never truncated. A line longer than `n-1` bytes
traps `LITHON-E0311`, a line containing a NUL byte traps `E0312`, and end of
input traps `E0308`; all are catchable, the first two as a `ValueError` and the
last as an `EOFError`, through the shared trap classifier both tiers already use.
A NUL is refused rather than stored because a `str[N]` is NUL-terminated: the
bytes after it would be written but could never be seen, which is the silent
loss this language refuses everywhere else. It is checked before length, since
the content is invalid at any limit, and the line is read one byte at a time so
the NUL is actually seen (a C-string reader would stop at it). The read removes the line terminator (`\n`, and a `\r` before it),
keeps all other bytes including spaces, treats an empty line as the empty string
and accepts a last line with no newline. It rewrites the whole run, content then
zeros, the state a string literal leaves, so a shorter line leaves no old tail.

One IR op carries it: `inputstr <run>, <hex prompt>, <n>`, with the destination a
named run like `streq`'s operands, no register operands and no result. The
interpreter and the JIT helper both call a single `read_str_line`, so the prompt
and flush, end-of-input, the length decision and the bytes written agree by
construction rather than by testing; the helper reads a line of any length
without a fixed buffer, so an over-long line is detected by its true length. The
JIT passes the frame base, a descriptor naming the run's offset, capacity and
limit, and the prompt from the string pool, and receives a kind code (0 ok, 1
too long, 2 NUL byte, 3 end of input) which it routes through the same trap emitter every
other trap uses, so a `try` handler is entered identically in both tiers. The
typechecker requires the destination to be a definitely assigned `str` and
re-checks `1 <= n <= N`, so hand-written IR cannot bypass the frontend. The op
is registered as register-clobbering and as a use of the run it names, so the
optimizer neither keeps a value across it in a caller-saved register nor deletes
the run's declaration.

Verification at the close of this stage: typed regression 64/64 (including
`str_input` with its `.in` file), general regression 34/34, tier differential
249/249 (native 137, interpreter fallback 0) with eight hand-written `inputstr`
pins (fits, empty line, CRLF, no final newline, overwrite clears the tail, spaces
kept, too long, end of input) comparing the JIT with the interpreter byte for
byte on stdout and stderr, direct-Phi 98 matched / 0 known gaps / 0 mismatched,
error-code checks 219 (eight new `E0115`/`E0116` refusals), object-runtime ctest
107 ok, and the full `tools/verify_all.sh` gate green.

## 4.13 — constants

`const var: type[size] = value` declares a name that is assigned exactly once, by
its initializer. The stage adds no IR opcode, checker rule or runtime ABI: a
constant lowers to an ordinary declaration and the frontend declines to let
anything write to it, exactly as 4.10.7 enforces nullability without a runtime
representation.

`const` is not Python syntax, and the frontend parses with Python's `ast`, so a
single entry point, `parse_source`, removes the keyword first. It uses the
tokenizer, so a string or comment containing the word is never touched, matches
only `const NAME :` at the start of a logical line, preserves every line number,
and marks the resulting statement. `const x = 5` (no type) is refused here. The
frontend's `main` and both library entry points use it.

Everything that could change a constant afterwards is refused with `E0143`:
assigning or augmenting the name, redeclaring it (as a variable, or as a second
constant, or over an existing variable), an element or field store rooted at it,
`append`, a string re-read, using it as a `for` target (the loop assigns it every
iteration), and `addressof` (a pointer could write through it). A struct has no
value to initialise it with, a pointer's address would let the pointee change, and
a class reference names an object a method could still mutate, so those three are
refused for now. A module constant reserves its name: a parameter or local of the
same name is refused, so it can never be shadowed by accident.

Folding is what makes a constant useful. A scalar constant whose initializer is a
literal that fits its declared type is substituted at every read, and because an
immutable literal is always safe to inline, a *module-level* one is visible
inside every function even though ordinary module variables are not. The same map
resolves a capacity written as a name in `list`, `tuple`, `str` and `dict`
annotations, and `literal_int`, so a constant can be a `range` bound (including
the proof a narrow 4.11 counter needs), a string limit in `str(n, input(...))`
or a pointer offset. A constant of any other type is an ordinary immutable
variable in its own scope. Constant *expressions* are not folded yet.

Verification at the close of this stage: typed regression `const_basic` agrees with
CPython (run on the program with the keyword removed) in the interpreter, in
strict native and in the default tiered mode; the error-code checks add seventeen
`E0143` refusals; the full gate is run at the end of the round.

## 4.14 — unsigned integers

`i: uint[size] = value` is a fixed-width unsigned integer, `size` 8, 16, 32 or 64,
holding `0 .. 2^size-1`. It was designed with four decisions: an out-of-range
result **traps** (E0313) and never wraps, `uint` and `int` **never mix**
implicitly, the first stage is **scalars only**, and `0` is a valid value with the
full range including `uint[64]` up to `2^64-1`.

A `uint` has no IR type. The IR sees the 64-bit integer that holds it, because an
unsigned value is the same bits, so the checker, the interpreter, the JIT and the
encoder are untouched. The frontend does all of the typing, which it reads off the
AST with a pure probe (`_peek_uint_width`) instead of tagging registers, so every
consumer decides at the node it is looking at. Only two things were added below
the frontend: `utrap %cond`, which traps E0313 when its bool operand is true and
is the one op that turns "the exact result left the range" into the trap, and the
`print_u`/`print_bare_u` builtins, which read the integer formatter's argument as
unsigned (`%llu`) for a `uint[64]` at or above `2^63`.

Mixing: arithmetic or a comparison between a `uint` and an `int` or `float`, a
signed value into a `uint`, and a negative or too-large literal are refused
(E0144). The allowed flows are a non-negative literal wherever a `uint` is accepted
(it takes the other operand's width and must fit it) and a `uint` narrower than 64
bits widening into an `int[64]`, which is exact. Between `uint`s a result takes the
wider width and a narrowing store is refused. Parameters and returns carry the
width (a function's return type is recorded so a call's result is known to be
unsigned), and `const` accepts `uint` with a literal initializer and folds it.

Lowering. For `uint[8|16|32]` the operands are non-negative int64 values, so the
signed op is exact and the result is range-checked: `gt r, max` for a sum, `lt r, 0`
for a difference, and both for a product (a `uint[32]` product can pass int64 and
wraps negative). For `uint[64]` the signed word cannot say "unsigned", so it is
built from signed operations: unsigned order is signed order with the sign bit
flipped on both sides (`bxor` with `2^63`), which also detects a sum's carry
(`s < a`) and a difference's borrow (`a < b`); a product is assembled from 32-bit
halves and traps if both high halves are set, a cross term or their sum needs more
than 32 bits, or adding the shifted terms carries; `%` handles `b >= 2^63` (the
quotient is 0 or 1), both operands below `2^63` (the signed `mod`), and `a >= 2^63`
with `b < 2^63` (`2*(a>>1 % b) + (a&1)`, subtracting `b` once if needed), each in its
own block so a signed `mod` never receives an operand it would fault on, with
`1 % b` first so a zero divisor traps like the signed operator; `>>` is logical (the
IR's `shr` is arithmetic) by masking one shift first. `/` is true division, hence a
float, and is refused for `uint[64]`; there is no integer division in Lithon. A left
shift traps if shifting back does not recover the operand.

Scope. Locals, free-function parameters and returns, constants and `print` only.
A `uint` is refused (E0144, or the existing struct/method/pointer diagnostic) as a
container element, a struct field, an enum payload, a class attribute, a method
parameter, a pointer pointee, an argument to `abs`/`int`/`float`/`wrap_*`/`syscall`
or an if-expression arm; a `uint[64]` additionally cannot index a list or bound a
`range`, because a value at or above `2^63` would be read as negative.

Two defects surfaced while verifying it, both fixed. (1) `fold_constants` folded a
plain `add`/`sub`/`mul` of two constants with wrap semantics on the assumption that
the checker had already refused an overflowing pair; with the SSA pipeline a
variable's constant value reaches the folder after that check, so
`4294967296 * 4294967296` printed `0` under `--ssa`/`--direct-phis` instead of
trapping E0303. A plain op whose exact result leaves int64 is no longer folded and
traps at run time on every path. (2) The `uint[32]` multiply's range check now also
catches the negative wrapped value rather than depending on that trap.

Verification at the close of this stage: `tools/check_uint.py` runs every operator
(`+ - * % & | ^ << >>` and all six comparisons) at every width over edge-heavy
values against an exact Python-integer oracle, and every one of its 12,177 cases
agrees on all four execution paths (interpreter, native, SSA, direct Phi); the
typed regressions `uint_basic` (CPython-checked: a Fibonacci that crosses `2^63` and
ends at `F(93)`, `20!`, and Euclid's algorithm with operands above `2^63`) and
`uint_traps` (hand-derived) pass in every tier; the error-code checks add the
`E0144` refusals and four byte-identical `E0313` traps.

## 4.15 — modules and the entry point

A program can now be split across `.lio` files, with a fixed entry point:
`if __name__ == '__main__': ...`. It was built level by level, as requested:
functions, then classes, then constants.

**No run-time import.** The stage adds no IR, checker or runtime change. A linker in
the frontend (`link_program`) resolves every import at compile time, parses each
file once, and merges the files into the single AST `build_program` already
compiles, so both tiers see an ordinary one-module program and an import costs
nothing when it runs. Imported symbols are renamed with their module's path
(`modules__mathlib__square`; a leading underscore in a module name is guarded
because `_name` is reserved for pointer variables), and calls are rewritten to the
renamed symbol wherever they appear.

**The entry guard.** `if __name__ == '__main__':` (either operand order, no `else`)
is the one form. In the program being run its body is spliced in place and runs; in
an imported file it is dropped, so a library can keep a demo or self-test that never
runs when imported. `__name__` is not a value and is refused anywhere but exactly
that guard. A script with no guard is unchanged and runs top to bottom.

**Imports.** `import m`, `import a.b`, `import m.f`, `from m import f` and `as`
aliases. A dotted name that is a file wins over one that is a member, so
`import prog1.func1` names the function `func1` of `prog1` unless
`prog1/func1.lio` exists. A directory is a package; no `__init__` file is needed.
The search order for a module is the importing file's directory, the directory of
the program being run (as a script's directory is for Python, so a library in
`src/` can import a sibling of the entry program), each `-I dir` in order, then each
`LITHON_PATH` directory. A file is keyed by its real path, so it is parsed and
linked once however many files import it (a diamond), and an import cycle is
detected on the import stack and refused with the chain.

**Levels.**
1. *Functions.* A module's own functions are visible by their source name inside
   it, by qualified name (`m.f`) or bound name (`f`, `g` for `as g`) in an importer.
2. *Classes.* A class, struct or enum (all `ClassDef`) is renamed and every place a
   type is spelled is rewritten: annotations, constructor calls, base lists,
   `isinstance`, classmethod calls and enum `match` patterns. A subclass in one
   module of a class in another works, including virtual dispatch across the
   boundary, because everything is one program with one class table.
3. *Constants.* A scalar (`int`, `uint`, `float`, `bool`) constant with a literal
   value is exported through the same machinery as 4.13's module constants: it is
   renamed, registered in the fold maps and so folds into every use, including a
   container or string capacity. An earlier integer constant is itself a literal to a
   later `const`, so `const M: int[64] = m.N` folds. A string, list or other constant
   is local to its own scope, as every variable is, so it cannot be defined in an
   imported file.

**An imported file only defines.** Functions, classes, structs, enums, literal
scalar constants, imports and the guard are allowed; any other top-level statement is
refused (E0145), because running code on import needs an initialisation order and
state shared between files. Also refused: `from m import *`, a relative import, an
import inside a function or block, two imports binding one name, an imported name
that collides with a definition of the importer, a type function, and a parameter or
local that reuses a name the module defines or imports (it would otherwise be
silently rewritten to the import).

**Only what is reachable is linked.** After renaming, the linker walks the merged
program from everything in the entry file and links an imported function, class or
constant only if reachable code names it, keeping each module's definitions in
source order (a base class still precedes its subclasses). A program importing 300
files of 20 functions each and calling three of them links in about 1.4 s, dominated
by parsing, and compiles to an IR of exactly those three functions.

Verification at the close of this stage: three typed regressions
(`modules_functions`, `modules_classes`, `modules_constants`, with fixtures under
`tests/typed_regression/modules/`) pass in every tier, and the error-code checks add
twenty module cases that build their multi-file layouts on the fly — fifteen
refusals (each `E0145`, one `E0143` for a write through an imported constant) and
nine acceptances run on both tiers, covering a sibling library, a dotted package, a
library importing a sibling of the entry program, `-I`, `LITHON_PATH`, the guard of
an imported module not running, a diamond linked once, same-named helpers in two
modules, a base and subclass in different modules, an imported constant as a
capacity, and an underscore-named module.

## 4.17 — x86-32

A second native backend for 32-bit hosts, written independently of the x86-64 one.

- **Everything is a pair.** IR temporaries are always 64-bit (a narrow width is only checked at `store`), so even `int[32]` programs do 64-bit arithmetic. Each value and variable is an 8-byte frame slot (low dword at +0), computed in `EAX`/`ECX`/`EDX`.
- **Conventions.** JIT-to-JIT calls are cdecl: arguments pushed right to left as pairs, caller pops, result in `EDX:EAX`. Host calls are cdecl and use only caller-saved registers. `kStackAlign` is 4 on Win32 and 16 on System V i386; every call pads to it (the System V path has not been executed here).
- **Same semantics.** Identical `E0303` (add/sub inline with `jno`, checked `mul` through a helper using `__builtin_mul_overflow`), `E0310`, `E0313`, shift-count and zero-divisor messages, the same `INT64_MIN % -1 == 0`, the `E0401` depth guard on the shared `g_call_depth`, and the same print formats.
- **Floats (4.17.2).** A float is the same 8-byte pair, so loads, stores, calls and returns are unchanged. Arithmetic is inline SSE2 (`addsd`/`subsd`/`mulsd`/`divsd`); an int operand is converted by a helper first. `<`/`>` use `seta` after `ucomisd` (unordered sets CF, so NaN is false), `==` is `sete`+`setnp`, `!=` is `setne`+`setp`. A zero divisor (±0.0) is detected from the bit pattern; `%` is `fmod` normalised to +0.0; `int()` traps `E0309`. Doubles come back from helpers through an out-pointer, not the x87 stack. The 32-bit host must be built `-msse2 -mfpmath=sse`: i686 defaults to x87, whose 80-bit intermediates double-round and would differ in the last bit from the interpreter.
- **Heap types (4.17.3).** The frame layout of variables, containers, `str`s, dicts and structs comes from the x86-64 `RegisterAllocator` (promotion off), so offsets and strides agree by construction and the print/str/dict descriptors are shared host structs with `EBP` as the base. Element access is `[ebp + idx*scale + disp]` with a per-level bounds check (a 64-bit index is in range exactly when its high dword is 0 and its low dword is below the capacity, unsigned). Dict probing is a host helper with the same hash and linear probe as the inline x86-64 code, so bucket order is identical. Objects keep their 8-byte header and attribute slots (a reference is the low dword of a pair), so the frontend's layout is unchanged. A runtime-called destructor receives one C pointer, so a `$c` thunk widens it to the JIT's argument pair. `try`/`except` is routed exactly as on x86-64 (per-block region snapshots; a trap jumps to its handler). This work found a pre-existing x86-64 bug: a narrow `int` dict value was zero-extended; fixed, with a regression test.
- **Speed (4.17.5).** Constants that every user can read as an immediate are never stored (reading the slot of one throws, so a missed site is a refusal, not a miscompile); a single-use result is forwarded in `EAX:EDX`/`XMM0`; an integer compare whose only user is the next `Branch` is fused through the flags; checked `*` and `%` have an inline path for sign-extended 32-bit operands (`imul`, `idiv`, divisor `-1` excluded); float constants are read from a pool; parameters are used where the caller pushed them; the call-depth counter is a 32-bit add. Remaining gap: no general register allocator.
- **Speed, second round (4.17.6).** Trap sites jump out of line (`jcc` to one shared stub per message, or directly to a catching `try` handler), constant shifts are inline `shld`/`shrd`, and the hottest scalar `int` variable (by loop-weighted use count) lives in `EBX:ESI`, saved in the frame and restored before every return. No general allocator yet.
- **Exported conventions.** `compile_module(m, export_thunks)` adds `<fn>$cdecl`/`$stdcall`/`$fastcall` adapters (cdecl: ST0 for a double result; stdcall: `ret 8n`; fastcall: two int/bool parameters of at most 32 bits in `ECX`/`EDX`). Checked from C with real convention attributes on both targets, sampling `ESP` around each call.
- **System V i386.** Executed natively as a static 32-bit ELF. A `LITHON_CHECK_ALIGN=1` probe (`int3` unless `ESP` is 16-byte aligned at a call) found a real misalignment in the destructor thunk that glibc never faulted on. `ObjectHeader` is `alignas(8)` with the pointer overlaid on an 8-byte slot so the header is 16 bytes on i386 Linux too. Raw `syscall` is `-ENOSYS` there.
- **Refusal, not guessing.** A float used as a condition, `Phi` nodes, and the few constructs listed in the README Todo throw at compile time and the caller falls back to the interpreter, as with the x86-64 backend. The x86-64 backend now also refuses inside a 32-bit process; before this it emitted x86-64 code and crashed when run.
- **Verification.** `tools/check_x86_32_encoding.py` disassembles every encoder form with `objdump` (87 forms). `tools/fuzz_x86_32.py` fuzzes 240 random programs (int, float, mod, bitwise) against the 64-bit interpreter. `tools/check_win64.py` runs the 32-bit `tier_runner` under wine: 55 programs (corpus plus `tests/x86_32`, which covers 64-bit edges, every trap and recursion overflow) compile natively and must equal the 64-bit interpreter in stdout, stderr and exit status; a floor on the native count stops a silent fallback. Breaking the 64-bit compare makes it fail.
- **Portability fix.** The type checker used `__int128` for the shift range check, which 32-bit targets lack; it now uses a small `Wide` type on every target.
- **Not done.** No register allocator (every value is memory); heap types and classes (4.17.3); stdcall/fastcall.

## 4.16 — Microsoft x64 ABI (the C boundary)

The JIT now follows the Windows calling convention wherever it meets C code, verified on a real MinGW build run under wine (`tools/check_win64.py`).

- **Shadow space.** Every host call goes through `emit_host_call`, which reserves the 32 bytes the callee may spill into (zero on System V). Seventeen call sites lacked it. JIT-to-JIT calls do not use it.
- **Callee-saved XMM.** `XMM12`–`XMM15` are scratch for float code but nonvolatile on Windows. A function that uses floats saves and restores them as full 128-bit values (`movups`, since a `movsd` load zeroes the upper half). `RBX`, `RDI`, `RSI`, `R12`–`R15` were already preserved.
- **Probe.** `src/jit/win64_abi_probe.cpp` loads sentinels into every callee-saved register, calls JIT functions through the Win64 convention and compares them afterwards. Six cases cover float, integer, host-call and mixed-argument functions.
- **Negative controls.** Disabling the XMM save makes the probe report `xmm12`–`xmm15` clobbered. Disabling the shadow space garbles program output (the suites are built `-O0` so helpers spill into it).
- **Stack size.** The `E0401` guard (4096 frames) assumes an 8 MiB stack. A Windows executable gets 1–2 MiB, so the Windows interpreter crashed on `tail_recursion.lio`. Windows binaries now link 8 MiB.
- **Portable executable memory.** `tier_runner` uses `ExecutableBuffer` (`VirtualAlloc` on Windows).
- **Incidental fixes.** `fold_constants` wrapped overflowing constant `mul`/`add`/`sub` under `--ssa` (now left to trap `E0303`). It also now drops the phi operand of a removed edge. The phi coalescer's stale alias is fixed.
- **Unchanged.** The internal JIT-to-JIT convention (two register arguments, then the stack). Four positional registers would shrink the Windows temp pool to `RAX` (see Todo 4.16b).

## 6.11 — functions that return a string, and the error display

**Str-returning functions.** `def f(...) -> str[N]` emits no IR function. The pre-pass records it in `STR_FN_DEFS` and validates its parameters (annotated `int`/`uint`/`float`/`bool` only, E0124). At a call site `_str_call_place` expands the body the way a struct method is inlined: locals and parameters are renamed with a per-call prefix (`_MethodSubst`), the result is a fresh `store <prefix>ret : str[N]` (which zeroes it, so a call in a loop starts clean), parameters are bound by annotated declarations, and `return` goes through the method return stack to `_emit_str_return`, which writes a literal byte by byte, or copies a str place with a generated copy (unrolled up to 16 bytes, a counted loop above). The expansion is cached per call node, so a place resolved twice (for example both operands of `==`) runs the body once. The buffer's name is the call's place: `_str_place_name`, `_container_print_arg`, the declaration and assignment paths all accept it. Recursion is refused with the chain, as for methods. A call used as a scalar is refused (E0114) with the list of accepted places. Because it is purely a frontend rewrite, the x86-64, Win32 and System V i386 backends needed no change. Added alongside: `s == "literal"` (the literal goes into a shared run like a `find` needle) and `s = "literal"` on an existing str (the whole-string write a struct field already had).

**Error display.** A refusal is still a `NotImplementedError` whose message starts `LITHON-Exxxx: ...` and is never edited. Every AST node is tagged with its file when `parse_source` parses it (`_lithon_file`), and `parse_source` records the source text and the columns it removed from `const` lines. `build_stmt` and `build_expr` are thin wrappers that attach the position of the node being built to a refusal (`LithonRefusal.loc`; the innermost node wins, a compiler-synthesised node without a real position defers to its parent, a statement is underlined on its first line). Errors raised outside a statement build (layouts, headers) are positioned from the top-level statement being processed. `format_diagnostic` renders code, message (wrapped, nothing dropped), `--> file:line:col`, the source line with a caret under the right columns (UTF-8 byte offsets converted to characters, tabs expanded), the notes, a help line and a docs pointer; colour only on request. An error inside an imported module points into that file. Syntax errors are rendered the same way, and the tokenizer's own failures (an unclosed bracket) are turned into `SyntaxError` instead of a traceback. For the runner's output, `format_runner_errors` drops the legacy `RCR error:` tag, positions a type-checker error from its context (`declaration of 'x'`, `return in 'f'`, `argument n to 'f'`) by looking it up in the parsed trees, and gives a coded runtime trap a help line.  The type checker's range arithmetic is now saturating (it overflowed `int64` on a full-range `int[64]` and printed a nonsense range).

**6.11 follow-up — string parameters, whole-string copies, and positions in the IR.** `INLINE_FN_DEFS` now holds every function that takes *or* returns a `str` (header rules in `_validate_inline_header`: annotated int/uint/float/bool/str parameters; a str, int, float or bool result). A str parameter is bound by an annotated declaration from the argument string, i.e. a copy (`s: str[N] = t`, now supported along with `s = t` and `s = "literal"`), so the callee cannot change the caller's string; a scalar result is carried in a result local like a method's. Each inlined function is expanded once into a scratch builder with placeholder arguments at definition time, so an error in a function that is never called, or recursion, is reported at its definition. Error messages show the programmer's parameter names, not the per-call renamed locals.

*Positions.* `ir::Instr` gained `src`/`line`, `ir::Module` a `sources` table, and the IR text `source N path` plus a trailing ` @N:line` (stripped by the parser before the instruction is read). The frontend tracks the node being built (`cur_pos`, innermost node wins) and writes the suffix only when `build_program(locations=True)` is requested, so the default IR, and the website's committed IR, are unchanged. The type checker's `RCRError` carries the position of `cur_` (the instruction being checked). The interpreter sets `ir::g_cur_pos` before each instruction; native code passes `pack_pos(src, line)` as a second argument to `host_report_error` (x86-64: one more immediate load per trap site; x86-32: one stub per message *and* position), which prints `  --> path:line` after the message exactly as the driver prints it for the interpreter, so stderr stays identical between tiers. `host_report_error` now also terminates a message that was stored without a newline (E0309 and others), which the interpreter always did. `input()` always reports through its error code so a failed read traps at its own site. Verified by running the tier diff, the error-code checks, the typed suite and both 32-bit targets with `LITHON_LOCATIONS=1`.
