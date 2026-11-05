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
| `E0111` | 1 (static refusal) | Bad container capacity / literal count / `str[N]` capacity or literal length (including a `__str__`/`__repr__` literal longer than its declared result) |
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
| `E0134` | 1 (static refusal) | Method definition (a `def` in a struct/enum/class body): a decorator other than a bare `@property`/`@classmethod`, more than one decorator, a decorated reserved/special method, `/`/`*`/`**`/defaults, a missing or annotated `self`/`cls`, an unannotated or non-scalar parameter/return, a property with arguments or no result type, a duplicate name, a name colliding with a field or variant, an unsupported class special method, a comparison dunder without one class-reference parameter and a `bool` result, or a `__str__`/`__repr__` body that is not one string-literal/scalar-f-string return |
| `E0135` | 1 (static refusal) | Method application and `super()` (4.9/4.10): an unknown method, a field where a method is called, a non-struct/enum/class receiver, keyword/starred arguments, an arity mismatch, a recursive inline, a method with no result used as a value, an incompatible inherited signature or changed method kind, an invalid `super()` form/target, a missing ordering method, an explicit `__str__`/`__repr__` call, an object with neither text method printed, a property assignment or call, a classmethod called on an instance, or an ordinary method called on a class name |
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
