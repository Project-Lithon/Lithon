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
| `E0126` | 1 (static refusal) | Struct definition (must derive from `struct`; fields are annotations only) |
| `E0127` | 1 (static refusal) | Struct field type (int[w]/float[64]/bool or an earlier struct; no containers/pointers) |
| `E0128` | 1 (static refusal) | Struct field access (unknown field, a whole struct used where a value is needed, or a `str` field reached through a pointer into the struct) |
| `E0129` | 1 (static refusal) | Enum definition (must derive from `enum`; variants are bare unit names, payload annotations, or inline `class V(struct)`) |
| `E0130` | 1 (static refusal) | Enum variant construction (unknown variant, wrong payload shape, unknown/missing/duplicate struct-payload field) |
| `E0131` | 1 (static refusal) | `match` on an enum (bad subject/pattern, non-exhaustive, a guard, a wildcard that is not last, or an enum used where a value is needed) |
| `E0132` | 1 (static refusal) | Type-function definition (`-> newtype`): bad signature, no body or more than one statement, a body that is not a one-element `{ T }`, a duplicate name, or a name colliding with a runtime function/struct/enum |
| `E0133` | 1 (static refusal) | Type-function application: an unknown or non-call name, keyword/starred arguments, an arity mismatch, or a recursive (direct or mutual) expansion |
| `E0134` | 1 (static refusal) | Method definition (a `def` in a struct/enum body): a decorator, `/`/`*`/`**`/defaults, a missing or annotated `self`, an unannotated or non-scalar parameter/return, a duplicate name, or a name colliding with a field or variant |
| `E0135` | 1 (static refusal) | Method application: an unknown method, a field where a method is called, a non-struct/enum receiver, keyword/starred arguments, an arity mismatch, a recursive inline, or a method with no result used as a value |
| `E0140` | 1 (static refusal) | Class instance semantics (4.10.1/4.10.2/4.10.3): a bare class name used where a value is needed, a class-typed variable initialized with a non-class value, an identity compare between two unrelated classes, an attempt to instantiate an abstract class, an attribute left uninitialized on some path through `__init__`, a bare declaration or `append` on a class-typed container, a shorter class-typed literal, storing a non-class or unrelated class reference into a class-typed container element, or membership of a non-class or unrelated class in a class-typed container |
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