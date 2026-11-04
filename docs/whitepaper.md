# Lithon Whitepaper

Seed content from `docs/lithon_error_system.md` (section 4c): the error-code
table. Every code below should eventually grow a one-paragraph entry here.

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
| `E0111` | 1 (static refusal) | Bad container capacity / literal count / `str[N]` capacity or literal length |
| `E0112` | 1 (static refusal) | Container element must be a plain scalar |
| `E0113` | 1 (static refusal) | Pointer (`ptr[T]`) declaration rules (4.4) |
| `E0114` | 1 (static refusal) | Unsupported or restricted program construct (including a non-literal `str[N]` value, or a whole `str` / `str` pointee used where a value is needed) |
| `E0115` | 1 (static refusal) | Unsupported builtin call form |
| `E0116` | 1 (static refusal) | Builtin argument rules (`valueof`/`addressof`/`wrap_*`) |
| `E0117` | 1 (static refusal) | Indexing / container-subscript restrictions |
| `E0118` | 1 (static refusal) | `else` clause on a `while`/`for` loop |
| `E0119` | 1 (static refusal) | `for`-loop form restrictions (including `enumerate`/`zip` over a class-typed container) |
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
| `E0134` | 1 (static refusal) | Method definition (a `def` in a struct/enum body): a decorator, `/`/`*`/`**`/defaults, a missing or annotated `self`, an unannotated or non-scalar parameter/return, a duplicate name, or a name colliding with a field or variant |
| `E0135` | 1 (static refusal) | Method application and `super()` (4.9/4.10): an unknown method, a field where a method is called, a non-struct/enum/class receiver, keyword/starred arguments, an arity mismatch, a recursive inline, a method with no result used as a value, an incompatible inherited signature, or an invalid `super()` form/target |
| `E0140` | 1 (static refusal) | Class instance semantics (4.10.1/4.10.2/4.10.3/4.10.4): a bare class name used where a value is needed, a class-typed variable initialized with a non-class value, an identity compare between classes with no possible common declared subclass, an attempt to instantiate an abstract class, an attribute left uninitialized on some path through `__init__`, a bare declaration or `append` on a class-typed container, storing a non-class or unrelated class reference into a class-typed container element, or membership of a non-class or unrelated class in a class-typed container |
| `E0141` | 1 (static refusal) | Class attribute access (4.10.1): a read or write of an attribute the class does not declare (an instance has exactly its declared attributes) |
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
| `E0401` | 3 (dynamic runtime trap, resource) | Stack overflow |

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

`in`, `is`/`is not` and the default `==`/`!=` compare references, with the same
related-class rule as class identity (`E0140` for unrelated classes).
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
