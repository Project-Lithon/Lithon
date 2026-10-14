#!/usr/bin/env python3
"""
Lithon frontend: Python source -> Lithon IR text.

Uses Python's own `ast` module as a shortcut -- not a permanent
architecture choice, replaced by a native Lithon parser at M10.

Two modes, both supported by the same builder:
  - Untyped input (bare "x = 10", no annotations): emits IR exactly
    as before, with no type fields. Existing tests/programs/*.py
    (the M1/M2 regression suite) use this path unchanged.
  - Typed input (AnnAssign "x: int[8] = 10", typed function
    signatures): emits IR carrying type_kind/type_width per V1_SPEC
    0.6, consumed by the type-checker before interpretation.

Supports: assignment (typed and untyped), augmented assignment
(+=, -=, *=, /= on a simple name), int/float/bool literals,
+/-/*//, comparisons (single, non-chained), and/or (exactly two
operands), not, print(), if/elif/else, conditional expressions
(`a if cond else b`), while, for ... in range(...),
function calls, return.
"""
import ast
import copy
import sys


class Block:
    def __init__(self, label):
        self.label = label
        self.lines = []


def render_type_suffix(kind, width, elem_kind=None, elem_width=None,
                       key_kind=None, key_width=None):
    """Renders the trailing " : T" / " : T[N]" annotation the IR parser reads.

    A container is `list[int[64], 4]`: the capacity is the OUTER bracket and the
    element type is nested inside it, which is the opposite arrangement from a
    scalar's `int[64]` where the bracket holds the width. Keeping that shape is
    what lets `parse_type_string` on the C++ side recurse once and land on the
    same ParsedType either way.

    4.3. A dict is the one container with three fields, so it gets its own branch:
    `dict[int[64], bool, 8]` is key, then value, then bucket count. The value
    still renders through the ordinary path, so it lands in the same element
    fields the C++ side already reads.
    """
    if not kind:
        return ""
    if kind == "dict":
        if elem_kind is None or key_kind is None:
            raise NotImplementedError(
                f"LITHON-E0110: dict annotation is missing its key or value type")
        k = render_type_suffix(key_kind, key_width).lstrip(" :")
        v = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : dict[{k}, {v}, {width}]"
    if kind == "ptr":
        # 4.4. `ptr[int[64]]`: the pointee is nested one level, exactly like a
        # container's element, but no capacity follows it. The C++ side reads
        # the pointee back through the same recursion, so it stays in the same
        # element fields a container's element would use.
        if elem_kind is None:
            raise NotImplementedError("LITHON-E0110: ptr annotation is missing its pointee type")
        inner = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : ptr[{inner}]"
    if kind in CONTAINER_KINDS:
        if elem_kind is None:
            raise NotImplementedError(f"LITHON-E0110: {kind} annotation is missing its element type")
        inner = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : {kind}[{inner}, {width}]"
    if width is None or width == -1:
        return f" : {kind}"
    return f" : {kind}[{width}]"


# 4.8. A type function is a compile-time type constructor, a "structural
# typedef": `def Name(p, ...) -> NewType: return { T }`. Applying it substitutes
# the call's arguments for the parameters and re-parses the body through
# `parse_type_annotation`, so the result is an ordinary type -- a 6-tuple
# indistinguishable from the expansion spelled out. Two equal expansions are
# the same type, the result is interchangeable with the spelled-out form, and
# nothing about the type function survives into the IR.
#
# `NewType` is a reserved marker: a `def` returning it is a type function, not a
# runtime function, and emits no code. The body is exactly one type expression
# in a one-element set literal -- `{ T }`. `{}` parses as ast.Dict (empty) and a
# `{k: v}` display as a non-empty ast.Dict, so both are refused by kind, and a
# `{x for ...}` comprehension is an ast.SetComp; only an ast.Set of one element
# is a type body.
TYPE_FUNCTION_MARKER = "NewType"


class TypeFunction:
    """One `def Name(p, ...) -> NewType: return { T }` declaration."""

    __slots__ = ("name", "params", "body_node")

    def __init__(self, name, params, body_node):
        self.name = name
        self.params = params          # list of parameter names, in order
        self.body_node = body_node    # the single type expression inside { }


# Populated once per run by `build_type_functions`, before any annotation is
# parsed, because `parse_type_annotation` is the one choke point that resolves a
# type-function call. The two side tables are per-run working state: the cache
# memoizes a call's expansion by its structural shape (`ast.dump`, which ignores
# source positions), and the active stack names the type functions currently
# being expanded, which is what catches direct and mutual recursion.
TYPE_FUNCTIONS = {}
_TYPE_FUNC_CACHE = {}
_TYPE_FUNC_ACTIVE = []


class _TypeSubst(ast.NodeTransformer):
    """Replaces each parameter `Name` in a type-function body with a deep copy of
    the argument node the call supplied. A deep copy matters because one
    parameter may be used more than once (`list[t, n]` then `n` again), and the
    same node must not end up shared in the substituted tree."""

    def __init__(self, mapping):
        self.mapping = mapping

    def visit_Name(self, node):
        if node.id in self.mapping:
            return copy.deepcopy(self.mapping[node.id])
        return node


def _collect_method_locals(body):
    """Every name a method body binds: assignment, annotation, for-target and
    match-capture names. These are the names an inlining must rename so a
    method local can never capture or clobber a caller local.

    A Store-context `Name` covers assignment, annotation, augmented assignment,
    for-targets and comprehension targets. `match` captures are not `Name`
    nodes, so `MatchAs`/`MatchStar` are collected explicitly.
    """
    names = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
    return names


class _MethodSubst(ast.NodeTransformer):
    """Rewrites a method body for inlining: every occurrence of `self` becomes
    the receiver place, and every parameter/local name is renamed to a call-site
    unique name. Type annotations are left untouched -- they name types, never
    locals -- so a struct name that happens to match a local is not rewritten."""

    def __init__(self, rename, self_repl):
        self.rename = rename
        self.self_repl = self_repl

    def visit_Name(self, node):
        if node.id == "self" and self.self_repl is not None:
            return copy.deepcopy(self.self_repl)
        if node.id in self.rename:
            node.id = self.rename[node.id]
        return node

    def visit_AnnAssign(self, node):
        # Rename the target and the value, but not the annotation.
        node.target = self.visit(node.target)
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_MatchAs(self, node):
        if node.name in self.rename:
            node.name = self.rename[node.name]
        if node.pattern is not None:
            node.pattern = self.visit(node.pattern)
        return node

    def visit_MatchStar(self, node):
        if node.name in self.rename:
            node.name = self.rename[node.name]
        return node

def _expand_type_function(call):
    """Expands `Name(args...)` to the parsed type its body denotes.

    Refuses an unknown callee, a keyword argument, an arity mismatch, and a
    recursive type function, each by name, and refuses to build a type whose
    substituted body is not itself a valid type (that surfaces from
    `parse_type_annotation` on the substituted body).
    """
    func = call.func
    if not isinstance(func, ast.Name):
        raise NotImplementedError(
            "LITHON-E0133: a type function call must be a plain name, e.g. "
            "`Fixed(int[64], 4)` (4.8)")
    name = func.id
    tf = TYPE_FUNCTIONS.get(name)
    if tf is None:
        raise NotImplementedError(
            f"LITHON-E0133: '{name}(...)' is not a declared type function; declare it "
            f"with `def {name}(...) -> NewType: return {{ ... }}` (4.8)")
    if call.keywords:
        raise NotImplementedError(
            f"LITHON-E0133: type function '{name}' takes positional type arguments only (4.8)")
    if any(isinstance(a, ast.Starred) for a in call.args):
        raise NotImplementedError(
            f"LITHON-E0133: type function '{name}' cannot be applied with a starred "
            f"argument (4.8)")
    if len(call.args) != len(tf.params):
        raise NotImplementedError(
            f"LITHON-E0133: type function '{name}' takes {len(tf.params)} type "
            f"argument(s), got {len(call.args)} (4.8)")

    key = ast.dump(call)
    cached = _TYPE_FUNC_CACHE.get(key)
    if cached is not None:
        return cached

    if name in _TYPE_FUNC_ACTIVE:
        chain = " -> ".join(_TYPE_FUNC_ACTIVE + [name])
        raise NotImplementedError(
            f"LITHON-E0133: type function '{name}' is recursive ({chain}); a type function "
            f"must bottom out in a concrete type (4.8)")

    mapping = {p: a for p, a in zip(tf.params, call.args)}
    substituted = _TypeSubst(mapping).visit(copy.deepcopy(tf.body_node))
    _TYPE_FUNC_ACTIVE.append(name)
    try:
        result = parse_type_annotation(substituted)
    finally:
        _TYPE_FUNC_ACTIVE.pop()
    _TYPE_FUNC_CACHE[key] = result
    return result


def parse_type_annotation(node, structs=None):
    """Returns (kind, width, elem_kind, elem_width, key_kind, key_width) from an
    annotation AST node, or raises NotImplementedError for forms this frontend
    slice doesn't handle.
    elem_kind/elem_width are None for every scalar and set for every container.
     key_kind/key_width are set only for a dict.
    `structs` maps a struct type name to its layout. It is needed only to write
    `ptr[Point]`: a struct is named at the source but the IR carries no struct
    NAMES at all (a struct is `struct[N]`, kind plus byte size), so the name
    has to be resolved to its size here, before it can be rendered.
    Mirrors the C++ parse_type_string, kept independent since this is a separate
    scaffolding tool (frontend vs IR parser)."""
    if isinstance(node, ast.Call):
        # 4.8. A call in annotation position is a type function application. It
        # is resolved here, the single choke point every annotation flows
        # through, so a type function works anywhere an annotation is allowed:
        # a declaration, a parameter, a return, a container element, a struct
        # field, a dict key/value, or a pointer pointee.
        return _expand_type_function(node)

    if isinstance(node, ast.Name):
        if node.id == "bool":
            return "bool", -1, None, None, None, None
        if node.id in ("int", "float"):
            opts = "8, 16, 32 or 64" if node.id == "int" else "64"
            raise NotImplementedError(
                f"LITHON-E0105: {node.id} requires an explicit width -- "
                f"{node.id} must be {opts}")
        raise NotImplementedError(f"LITHON-E0109: type '{node.id}' requires an explicit size, e.g. {node.id}[64]")

    if isinstance(node, ast.Subscript):
        if not isinstance(node.value, ast.Name):
            raise NotImplementedError("LITHON-E0108: unsupported type annotation form")
        base = node.value.id
        if base == "dict":
            return _parse_dict_annotation(node.slice)
        if base == "ptr":
            return _parse_ptr_annotation(node.slice, structs)
        if base in CONTAINER_KINDS:
            return _parse_container_annotation(base, node.slice)
        if base not in ("int", "float", "str"):
            raise NotImplementedError(f"LITHON-E0109: unknown type '{base}'")
        size_node = node.slice
        if not isinstance(size_node, ast.Constant) or not isinstance(size_node.value, int):
            raise NotImplementedError(f"LITHON-E0109: {base}[N] requires a literal integer size")
        _require_supported_width(base, size_node.value)
        return base, size_node.value, None, None, None, None

    raise NotImplementedError("LITHON-E0108: unsupported type annotation form")


# E0105 (docs/lithon_error_system.md 4c): only int[8|16|32|64] and float[64]
# exist. Container capacities and dict bucket counts are not widths and are
# exempt; this is called on scalar leaves only.
_SCALAR_WIDTHS = {"int": (8, 16, 32, 64), "float": (64,)}


def _require_supported_width(base, width):
    allowed = _SCALAR_WIDTHS.get(base)
    if allowed is None or width in allowed:
        return
    if base == "int":
        raise NotImplementedError(
            f"LITHON-E0105: unsupported integer width {width} -- "
            "int must be 8, 16, 32 or 64")
    raise NotImplementedError(
        f"LITHON-E0105: unsupported float width {width} -- float must be 64")


def _parse_dict_annotation(slice_node):
    """Parses the `K, V, N` of `dict[K, V, N]`.

    Both element types and the bucket count are mandatory and literal. The
    bucket count has to be a compile-time constant because it is the mask that
    turns a key into a bucket, and a computed count would put a division in the
    hot path of every lookup. It is also required to be a power of two, so the
    mask is an and rather than a division.
    """
    if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != 3:
        raise NotImplementedError(
            "LITHON-E0111: dict[K, V, N] needs a key type, a value type and a literal bucket "
            "count, e.g. dict[int[64], int[64], 8]")
    key_node, val_node, cap_node = slice_node.elts
    if not isinstance(cap_node, ast.Constant) or not isinstance(cap_node.value, int) \
            or isinstance(cap_node.value, bool):
        raise NotImplementedError("LITHON-E0111: dict bucket count must be a literal integer")
    if cap_node.value <= 0:
        raise NotImplementedError(
            f"LITHON-E0111: dict bucket count must be positive, got {cap_node.value}")
    if cap_node.value & (cap_node.value - 1):
        raise NotImplementedError(
            f"LITHON-E0111: dict bucket count must be a power of two, got {cap_node.value}")
    key_kind, key_width, key_elem_kind, _, _, _ = parse_type_annotation(key_node)
    if key_elem_kind is not None:
        raise NotImplementedError("LITHON-E0112: a dict key cannot itself be a container")
    val_kind, val_width, val_elem_kind, _, _, _ = parse_type_annotation(val_node)
    if val_elem_kind is not None:
        raise NotImplementedError("LITHON-E0112: a dict value cannot itself be a container")
    return "dict", cap_node.value, val_kind, val_width, key_kind, key_width


def _parse_ptr_annotation(slice_node, structs=None):
    """Parses the `T` of `ptr[T]`, e.g. `ptr[int[64]]`.

    4.4. The pointee is a plain scalar, and it must carry its size. An address
    is a number that denotes one cell of one type; pointing at a container
    would mean the address knows the container's shape, which the pointer
    cannot carry (its type data is just a kind and a width on the IR side, and
    a stack slot address on the run side). So the pointee grants the pointer
    exactly one of int, float or bool at a stated width -- the width is what
    valueof needs to know how many bytes to read.

    4.4, second half. A struct is also a legal pointee: its bytes ARE its
    storage, so `addressof(pt)` names the blob and pointer arithmetic advances
    by the whole struct. The name is resolved to the layout's byte size here,
    because the IR carries `ptr[struct[N]]` and never a struct NAME -- which is
    also why the C++ side needs no change: `struct[N]` already parses.
    """
    if structs and isinstance(slice_node, ast.Name) and slice_node.id in structs:
        return "ptr", -1, "struct", structs[slice_node.id].size, None, None
    if not isinstance(slice_node, (ast.Name, ast.Subscript)):
        raise NotImplementedError("LITHON-E0113: ptr[T] needs a scalar pointee, e.g. ptr[int[64]] (4.4)")
    elem_kind, elem_width, elem_elem_kind, elem_elem_width, _, _ = \
        parse_type_annotation(slice_node)
    if elem_elem_kind is not None:
        raise NotImplementedError(
            f"LITHON-E0113: a ptr pointee is a scalar, not a container (4.4); "
            f"ptr[{elem_kind}[...]] does not point at anything single")
    if elem_kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"LITHON-E0113: ptr pointees are int, float or bool only, got {elem_kind} (4.4)")
    return "ptr", -1, elem_kind, elem_width, None, None


def _parse_container_annotation(base, slice_node):
    """Parses the `T, N` of `list[T, N]`.

    The capacity is mandatory and must be a literal integer, because it is the
    thing that makes a list fixed-capacity and heap-free: N decides the frame
    size at compile time. A computed capacity would mean the compiler cannot
    size the allocation, which is the entire reason this type exists, so it is
    rejected rather than deferred.
    """
    if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != 2:
        raise NotImplementedError(
            f"LITHON-E0111: {base}[T, N] needs an element type and a literal capacity, "
            f"e.g. {base}[int[64], 4]")
    elem_node, cap_node = slice_node.elts
    if not isinstance(cap_node, ast.Constant) or not isinstance(cap_node.value, int) \
            or isinstance(cap_node.value, bool):
        raise NotImplementedError(f"LITHON-E0111: {base}[T, N] requires a literal integer capacity")
    if cap_node.value <= 0:
        raise NotImplementedError(f"LITHON-E0111: {base} capacity must be positive, got {cap_node.value}")
    elem_kind, elem_width, _unused_key_kind, _unused_key_width, \
        elem_elem_kind, elem_elem_width = parse_type_annotation(elem_node)
    if elem_elem_kind is not None:
        # 4.1 accepts a nested container as a TYPE -- the type has to exist
        # before the flat-stride work that can lay one out -- but rejects it in
        # codegen, where the outer stride becomes sizeof(inner) and no SIB scale
        # can express it. Accepting it here keeps the type and the rejection in
        # their agreed places rather than duplicating the error in three layers.
        return base, cap_node.value, elem_kind, elem_width, None, None
    return base, cap_node.value, elem_kind, elem_width, None, None


# The synthesized module-level entry point is called `__main__`, which
# both execution tiers look up first (falling back to a plain `main` for
# hand-written IR). Because the entry point is not `main`, a user function
# named `main` is an ordinary function and cannot collide with it -- the
# old collision emitted two `function main` definitions, so `print(main())`
# silently ran the user's body as the program entry and printed nothing.
# A user function that does take the reserved entry name (`def __main__`)
# is emitted under a mangled name and calls to it are renamed to match, so
# the entry point is always unique.
ENTRY_POINT = "__main__"
MANGLED_PREFIX = "user_"

# Prefix for the temporary that holds the result of a conditional expression.
# The IR text format has no phi opcode, so `a if c else b` needs a variable both
# arms can store and the join can load. The name is compiler-generated, so no
# source annotation can exist for it; the type checker recognises the prefix
# and infers the type from the arms (is_lowered_merge_temp in
# src/typecheck/typecheck.cpp), which is why the two spellings must agree.
IF_EXPR_TEMP = "__ifexpr"

# One table, used by both BinOp and AugAssign. It used to be duplicated as a
# literal dict at each site, which is exactly the kind of drift that lets a
# new operator work in `a + b` and silently fail in `a += b`.
#
# `%` is `mod`, and is NOT the same shape as `div`: div always widens to float
# (a quotient generally is not an integer), while mod is typed like mul (int
# iff both operands are int) and keeps C's truncating remainder semantics.
# 4.1. The container kinds whose annotation nests an element type and a
# capacity, like list[T, N]. ptr[T] carries no capacity and is 4.4, so it is
# not in this set. Putting it here would demand a capacity it does not have.
#
# 4.2. tuple[T, N] joins it here, and that is the whole frontend change.
# A tuple has the same shape as a list. Same packed layout, same stride, same
# Index read. The only thing that separates them is that a tuple cannot be
# written, and that rule lives in the type checker. So the IR keeps one
# representation for a run of slots, and immutability is a compile time
# rejection instead of a second container kind threaded through the
# interpreter and codegen.
# 4.3. dict[K, V, N] joins it here too, and its annotation is the one shape that
# does not fit this set. A list and a tuple are `T, N`, two fields with the
# element type first. A dict is `K, V, N`, three fields, and it carries two
# element types rather than one. So it is listed here for the valueless
# declaration and the type suffix, but both of those have a dict branch ahead of
# the shared path, because the shared path cannot express a second type.
CONTAINER_KINDS = ("list", "tuple", "dict")

# 4.6. A struct is declared by subclassing the builtin `Struct` marker and
# annotating its fields. The marker has no behaviour; it is the spelling that
# distinguishes a struct definition from a class this frontend does not support.
STRUCT_BASE = "Struct"

# 4.7. An enum is declared by subclassing the builtin `Enum` marker, the same
# way a struct derives from `Struct`. Each member is a variant: a bare name
# (unit), an annotation (a payload), or an inline `class V(Struct)` (a struct
# payload). The tag/payload byte layout is computed here and frozen into the
# same fieldload/fieldstore instructions a struct uses, so the tiers never learn
# what a variant is.
ENUM_BASE = "Enum"


class StructLayout:
    """The packed byte layout of one struct type.

    The C++ side deliberately carries no field table: the IR only sees a struct
    as a byte blob sized on its declaration, with each field access naming a byte
    offset. So this layout is the canonical one -- offsets computed once here and
    frozen into the fieldload/fieldstore instructions -- which is what keeps the
    interpreter and the JIT reading the same bytes without either of them knowing
    what a field is called.
    """

    __slots__ = ("name", "fields", "size", "methods")

    def __init__(self, name):
        self.name = name
        self.fields = {}   # field name -> {kind, width, offset, byte_size, struct_name}
        self.size = 0
        # 4.9. Methods declared in the class body, name -> the FunctionDef. A
        # method is not a value and emits no code of its own: a call inlines it
        # at the call site, where the receiver is a concrete place.
        self.methods = {}


def _struct_field_type(struct_name, field_name, ann, structs, enums):
    """Resolves a field annotation to (kind, width, ref_name, byte_size).

    A field is int[w], float[64], bool, a struct, or an enum, each declared
    EARLIER. Requiring earlier declaration is what makes the size of a nested
    aggregate known here and, at the same time, makes a cycle impossible: a
    struct that cannot name itself or a later aggregate has no way to be
    infinitely large. The returned ref_name names the struct or enum a field of
    that kind embeds, and is None for a scalar.
    """
    if isinstance(ann, ast.Name):
        if ann.id in structs:
            return "struct", -1, ann.id, structs[ann.id].size
        if ann.id in enums:
            return "enum", -1, ann.id, enums[ann.id].size
        if ann.id not in ("int", "float", "bool"):
            raise NotImplementedError(
                f"LITHON-E0127: field '{field_name}' of struct '{struct_name}' has unknown type "
                f"'{ann.id}' -- a field is int[w], float[64], bool, a struct or an enum declared "
                f"earlier (4.6)")
    kind, width, elem_kind, elem_width, _key_kind, _key_width = parse_type_annotation(ann)
    if kind in CONTAINER_KINDS or kind == "ptr" or elem_kind is not None:
        raise NotImplementedError(
            f"LITHON-E0127: field '{field_name}' of struct '{struct_name}' is a {kind}; 4.6 "
            f"fields are int[w], float[64], bool, a plain struct or an enum -- a container or "
            f"pointer cannot be stored in a struct")
    if kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"LITHON-E0127: field '{field_name}' of struct '{struct_name}' has unsupported "
            f"type {kind} (4.6)")
    if kind == "bool":
        return "bool", -1, None, 1
    if width is None or width <= 0:
        raise NotImplementedError(
            f"LITHON-E0127: field '{field_name}' of struct '{struct_name}' needs a width, "
            f"e.g. {kind}[64] (4.6)")
    return kind, width, None, width // 8


def _method_scalar_type(ann, owner_name, method_name, what, structs, enums):
    """Validates that a method parameter or return annotation is a scalar.

    A method is inlined at the call site, so its parameters are evaluated once
    into locals and its result is a local: both must be scalar value types. A
    struct or enum is storage and cannot be one; a container or pointer is
    passed by value elsewhere and is out of scope for a method signature.
    """
    if isinstance(ann, ast.Name) and ann.id == "bool":
        return
    if isinstance(ann, ast.Name) and (ann.id in structs or ann.id in enums
                                      or ann.id == owner_name):
        if ann.id == owner_name:
            described = f"the enclosing type '{ann.id}'"
        else:
            described = f"the {'struct' if ann.id in structs else 'enum'} '{ann.id}'"
        raise NotImplementedError(
            f"LITHON-E0134: {what} of method '{owner_name}.{method_name}' is {described}; a "
            f"method takes and returns scalar int[w], float[64] or bool (4.9)")
    kind, width, elem_kind, elem_width, key_kind, key_width = parse_type_annotation(ann)
    if kind == "ptr" or elem_kind is not None or key_kind is not None:
        raise NotImplementedError(
            f"LITHON-E0134: {what} of method '{owner_name}.{method_name}' is a {kind}; a method "
            f"takes and returns scalar int[w], float[64] or bool (4.9)")
    if kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"LITHON-E0134: {what} of method '{owner_name}.{method_name}' is unsupported "
            f"({kind}); a method takes and returns scalar int[w], float[64] or bool (4.9)")


def _validate_method(stmt, owner_name, layout, structs, enums):
    """Validates one `def` in a struct or enum class body and stores it.

    The receiver is implicit: the first parameter must be the unannotated name
    `self`, and the rest are annotated scalars. Methods are not overloaded and
    there is no inheritance, so a name must be unique within the class and may
    not shadow a field or a variant. The body is validated when the method is
    inlined (it needs a receiver to resolve `self.field`); only the signature is
    frozen here.
    """
    method_name = stmt.name
    if stmt.decorator_list:
        raise NotImplementedError(
            f"LITHON-E0134: method '{owner_name}.{method_name}' cannot carry a decorator (4.9)")
    a = stmt.args
    if (a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg
            or a.defaults or a.kw_defaults):
        raise NotImplementedError(
            f"LITHON-E0134: method '{owner_name}.{method_name}' takes only plain positional "
            f"parameters -- no `/`, `*`, `**` or defaults (4.9)")
    if not a.args:
        raise NotImplementedError(
            f"LITHON-E0134: method '{owner_name}.{method_name}' must take `self` as its first "
            f"parameter (4.9)")
    self_arg = a.args[0]
    if self_arg.arg != "self":
        raise NotImplementedError(
            f"LITHON-E0134: the first parameter of method '{owner_name}.{method_name}' must be "
            f"named `self`, the implicit receiver (4.9)")
    if self_arg.annotation is not None:
        raise NotImplementedError(
            f"LITHON-E0134: `self` of method '{owner_name}.{method_name}' must be unannotated "
            f"-- it names the receiver, not a value (4.9)")
    for p in a.args[1:]:
        if p.annotation is None:
            raise NotImplementedError(
                f"LITHON-E0134: parameter '{p.arg}' of method '{owner_name}.{method_name}' must "
                f"be annotated, e.g. `{p.arg}: int[64]` (4.9)")
        _method_scalar_type(p.annotation, owner_name, method_name,
                            f"parameter '{p.arg}'", structs, enums)
    if stmt.returns is not None:
        _method_scalar_type(stmt.returns, owner_name, method_name, "return type", structs, enums)
    if method_name in getattr(layout, "fields", {}) \
            or method_name in getattr(layout, "variant_by_name", {}):
        raise NotImplementedError(
            f"LITHON-E0134: method '{owner_name}.{method_name}' collides with a field or "
            f"variant of the same name (4.9)")
    if method_name in layout.methods:
        raise NotImplementedError(
            f"LITHON-E0134: method '{owner_name}.{method_name}' is declared more than once (4.9)")
    layout.methods[method_name] = stmt


def _build_struct_layout(stmt, structs, enums):
    """Validates one `class Name(Struct)` body and inserts its StructLayout.

    Shared by the module-level struct pass and by an enum's inline struct
    variant, so the two spellings produce byte-identical layouts.
    """
    if stmt.name in structs or stmt.name in enums:
        raise NotImplementedError(
            f"LITHON-E0126: type '{stmt.name}' is declared more than once (4.6/4.7)")
    layout = StructLayout(stmt.name)
    offset = 0
    for member in stmt.body:
        if isinstance(member, ast.FunctionDef):
            # 4.9. A method: no field, no offset, no code of its own.
            _validate_method(member, stmt.name, layout, structs, enums)
            continue
        if not isinstance(member, ast.AnnAssign):
            raise NotImplementedError(
                f"LITHON-E0126: struct '{stmt.name}' may contain only field annotations "
                f"(`name: type`) and methods (`def`); found a {type(member).__name__} (4.6)")
        if not isinstance(member.target, ast.Name):
            raise NotImplementedError(
                f"LITHON-E0126: a field of struct '{stmt.name}' must be a plain name, not "
                f"a subscript or attribute (4.6)")
        field_name = member.target.id
        if field_name in layout.fields:
            raise NotImplementedError(
                f"LITHON-E0127: struct '{stmt.name}' declares field '{field_name}' more "
                f"than once (4.6)")
        kind, width, ref_name, byte_size = _struct_field_type(
            stmt.name, field_name, member.annotation, structs, enums)
        layout.fields[field_name] = {
            "kind": kind, "width": width, "offset": offset, "byte_size": byte_size,
            "struct_name": ref_name if kind == "struct" else None,
            "enum_name": ref_name if kind == "enum" else None,
        }
        offset += byte_size
    if not layout.fields:
        raise NotImplementedError(
            f"LITHON-E0126: struct '{stmt.name}' has no fields -- a struct needs at least "
            f"one int[w], float[64], bool, a nested struct or an enum field (4.6)")
    layout.size = offset
    structs[stmt.name] = layout
    return layout


class EnumLayout:
    """The packed byte layout of one enum type: `[tag][payload]`, no padding.

    Like a StructLayout this is the canonical layout -- the tag offset (always 0)
    and each payload offset are frozen into fieldload/fieldstore instructions, so
    the C++ side sees only bytes. The tag starts at 0 and is an int[8] while the
    enum has at most 128 variants (0..127, the positive range of a signed byte)
    and an int[16] beyond that. Its value is the variant's declaration index.
    The payload begins at the tag width; inactive payload bytes are undefined and
    are never read.
    """

    __slots__ = ("name", "variants", "variant_by_name", "tag_width",
                 "payload_offset", "size", "methods")

    def __init__(self, name):
        self.name = name
        self.variants = []        # [{name, tag, payload, payload_offset}]
        self.variant_by_name = {}
        self.tag_width = 8
        self.payload_offset = 1
        self.size = 0
        # 4.9. Methods declared in the class body, name -> the FunctionDef.
        self.methods = {}


def _enum_payload_type(enum_name, variant_name, ann, structs, enums):
    """Resolves a variant payload annotation to a descriptor, or None for unit.

    A payload is a scalar, or a struct declared earlier. A container payload
    would need a nested run that the flat byte blob cannot index, and an
    enum-valued payload would need nested construction; both are refused here
    rather than silently mis-laid-out.
    """
    if isinstance(ann, ast.Name) and ann.id in enums:
        raise NotImplementedError(
            f"LITHON-E0130: variant '{variant_name}' of enum '{enum_name}' has an enum-typed "
            f"payload; 4.7 payloads are a scalar or a struct (4.7)")
    if isinstance(ann, ast.Name) and ann.id in structs:
        return {"kind": "struct", "width": -1, "struct_name": ann.id,
                "byte_size": structs[ann.id].size}
    kind, width, elem_kind, elem_width, _key_kind, _key_width = parse_type_annotation(ann)
    if kind in CONTAINER_KINDS or kind == "ptr" or elem_kind is not None:
        raise NotImplementedError(
            f"LITHON-E0130: variant '{variant_name}' of enum '{enum_name}' has a {kind} payload; "
            f"4.7 payloads are a scalar, a struct or (later) a list -- a container is laid out "
            f"elsewhere and cannot live inside the enum blob (4.7)")
    if kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"LITHON-E0130: variant '{variant_name}' of enum '{enum_name}' has unsupported "
            f"payload type {kind} (4.7)")
    if kind == "bool":
        return {"kind": "bool", "width": -1, "struct_name": None, "byte_size": 1}
    if width is None or width <= 0:
        raise NotImplementedError(
            f"LITHON-E0130: variant '{variant_name}' of enum '{enum_name}' payload needs a "
            f"width, e.g. {kind}[64] (4.7)")
    return {"kind": kind, "width": width, "struct_name": None, "byte_size": width // 8}


def _build_enum_layout(stmt, structs, enums):
    """Validates one `class Name(Enum)` body and inserts its EnumLayout.

    A member is one of three variant spellings: a bare name (unit), an
    annotation `V: T` (a payload), or an inline `class V(Struct)` (a struct
    payload, equivalent to declaring that struct beforehand). Anything else --
    `V = 1`, `def`, a nested non-Struct class -- is refused with a name for the
    reason.
    """
    if stmt.name in structs or stmt.name in enums:
        raise NotImplementedError(
            f"LITHON-E0129: type '{stmt.name}' is declared more than once (4.6/4.7)")
    if not stmt.body:
        raise NotImplementedError(
            f"LITHON-E0129: enum '{stmt.name}' has no variants -- an enum needs at least one "
            f"variant (4.7)")
    layout = EnumLayout(stmt.name)
    max_payload = 0
    for member in stmt.body:
        payload = None
        if isinstance(member, ast.Expr) and isinstance(member.value, ast.Name):
            variant_name = member.value.id
        elif isinstance(member, ast.AnnAssign):
            if not isinstance(member.target, ast.Name):
                raise NotImplementedError(
                    f"LITHON-E0129: a variant of enum '{stmt.name}' must be a plain name (4.7)")
            if member.value is not None:
                raise NotImplementedError(
                    f"LITHON-E0129: variant '{member.target.id}' of enum '{stmt.name}' cannot "
                    f"take a value -- write `{member.target.id}: <type>` for a payload variant "
                    f"or `{member.target.id}` for a unit variant (4.7)")
            variant_name = member.target.id
            payload = _enum_payload_type(
                stmt.name, variant_name, member.annotation, structs, enums)
        elif isinstance(member, ast.ClassDef):
            if len(member.bases) != 1 or not isinstance(member.bases[0], ast.Name) \
                    or member.bases[0].id != STRUCT_BASE:
                raise NotImplementedError(
                    f"LITHON-E0129: inline variant class '{member.name}' of enum '{stmt.name}' "
                    f"must derive from Struct, e.g. `class {member.name}(Struct):` (4.7)")
            _build_struct_layout(member, structs, enums)
            variant_name = member.name
            payload = {"kind": "struct", "width": -1, "struct_name": variant_name,
                       "byte_size": structs[variant_name].size}
        elif isinstance(member, ast.FunctionDef):
            _validate_method(member, stmt.name, layout, structs, enums)
            continue
        else:
            raise NotImplementedError(
                f"LITHON-E0129: enum '{stmt.name}' members are bare unit names, payload "
                f"annotations (`V: type`) or inline `class V(Struct)` variants; found a "
                f"{type(member).__name__} (4.7)")
        if variant_name in layout.variant_by_name:
            raise NotImplementedError(
                f"LITHON-E0129: enum '{stmt.name}' declares variant '{variant_name}' more "
                f"than once (4.7)")
        if payload is not None and payload["kind"] == "struct":
            # Construction of a struct payload sets its fields by name and a
            # match binds them one leaf at a time, so every field must be a
            # scalar. A nested aggregate field would need nested construction
            # that this slice does not spell.
            for fname, f in structs[payload["struct_name"]].fields.items():
                if f["kind"] not in ("int", "float", "bool"):
                    raise NotImplementedError(
                        f"LITHON-E0130: struct payload '{payload['struct_name']}' of variant "
                        f"'{variant_name}' has a non-scalar field '{fname}'; 4.7 struct-payload "
                        f"fields must be scalars (4.7)")
        variant = {"name": variant_name, "tag": len(layout.variants),
                   "payload": payload, "payload_offset": None}
        layout.variants.append(variant)
        layout.variant_by_name[variant_name] = variant
        if payload is not None:
            max_payload = max(max_payload, payload["byte_size"])
    # The tag is signed, and a narrow int load sign-extends, so an int[8] tag
    # only round-trips variants 0..127. Crossing that needs int[16].
    if not layout.variant_by_name:
        raise NotImplementedError(
            f"LITHON-E0129: enum '{stmt.name}' has no variants -- an enum needs at least one "
            f"variant in addition to any methods (4.9)")
    layout.tag_width = 8 if len(layout.variants) <= 128 else 16
    layout.payload_offset = layout.tag_width // 8
    for variant in layout.variants:
        if variant["payload"] is not None:
            variant["payload_offset"] = layout.payload_offset
    layout.size = layout.payload_offset + max_payload
    enums[stmt.name] = layout
    return layout


def _is_type_function_def(stmt):
    """True for `def ... -> NewType:` -- the marker that makes a def a type
    function rather than a runtime function. Such a def emits no code and is
    resolved only when its name is applied in an annotation."""
    return (isinstance(stmt, ast.FunctionDef)
            and isinstance(stmt.returns, ast.Name)
            and stmt.returns.id == TYPE_FUNCTION_MARKER)


def _reject_storage_in_type_function_bodies(structs, enums):
    """Refuses a type-function body that names a struct or an enum.

    A type function composes the *value* types a normal annotation can spell --
    int, float, bool, containers, ptr and dict -- and it is resolved by
    `parse_type_annotation`, which does not know struct or enum names (those are
    storage and are recognised by the declaration sites instead). Naming one
    finds no value type and would surface as a bare "requires an explicit size",
    so it is refused here with the reason. A parameter that happens to share a
    struct's name shadows it and is not refused.
    """
    for tf in TYPE_FUNCTIONS.values():
        for node in ast.walk(tf.body_node):
            if not isinstance(node, ast.Name) or node.id in tf.params:
                continue
            if node.id in structs:
                kind = "struct"
            elif node.id in enums:
                kind = "enum"
            else:
                continue
            raise NotImplementedError(
                f"LITHON-E0132: type function '{tf.name}' body names the {kind} "
                f"'{node.id}'; a type function composes scalar, container, pointer and "
                f"dict types, not a struct or an enum (4.8)")


def _validate_type_function(stmt, registry):
    """Validates one type-function definition and inserts it into `registry`.

    The signature is plain positional parameter names (a type function's
    parameters are types or sizes, so they are unannotated and have no
    defaults), and the body is exactly one `return { <type> }` whose braces hold
    one type expression. A unit must bottom out: recursion is refused when the
    function is applied, not here, because a definition alone cannot be applied.
    """
    name = stmt.name
    if stmt.decorator_list:
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' cannot carry a decorator (4.8)")
    a = stmt.args
    if (a.posonlyargs or a.vararg or a.kwonlyargs or a.kwarg
            or a.defaults or a.kw_defaults):
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' takes only plain positional parameter "
            f"names -- no `/`, `*`, `**` or defaults (4.8)")
    for arg in a.args:
        if arg.annotation is not None:
            raise NotImplementedError(
                f"LITHON-E0132: parameter '{arg.arg}' of type function '{name}' must be "
                f"unannotated -- its parameters are types or sizes, not values (4.8)")
    if name in registry:
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' is declared more than once (4.8)")
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Return):
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' body must be a single `return {{ <type> }}` "
            f"(4.8)")
    ret = stmt.body[0]
    if ret.value is None:
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' must return a type expression, e.g. "
            f"`return {{ list[a, 4] }}` (4.8)")
    val = ret.value
    if isinstance(val, ast.Dict):
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' body must be `{{ <type> }}` holding one "
            f"type; `{{}}` is a dict and a dict display is not a type here (4.8)")
    if isinstance(val, ast.SetComp):
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' body cannot be a comprehension; write "
            f"`return {{ <type> }}` (4.8)")
    if not isinstance(val, ast.Set):
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' body must put a single type in braces, "
            f"e.g. `return {{ list[a, 4] }}` (4.8)")
    if len(val.elts) != 1:
        raise NotImplementedError(
            f"LITHON-E0132: type function '{name}' body must hold exactly one type "
            f"expression, found {len(val.elts)} (4.8)")
    registry[name] = TypeFunction(name, [arg.arg for arg in a.args], val.elts[0])


def build_type_functions(tree):
    """Collects every `def Name(...) -> NewType: ...` into a registry.

    Run before `build_type_registry` so a struct field, enum payload, parameter
    or return can name a type function. The names share one namespace with
    structs and enums; the collision of the two registries is checked in
    `build_program` once both exist.
    """
    registry = {}
    for stmt in tree.body:
        if _is_type_function_def(stmt):
            _validate_type_function(stmt, registry)
    return registry


def build_type_registry(tree):
    """Collects every `class Foo(Struct)` and `class Foo(Enum)` into two
    registries, in a single source-order pass.

    One pass is what lets either kind name the other: a struct field may be an
    enum declared earlier, an enum payload may be a struct declared earlier, and
    an inline `class V(Struct)` variant is registered (and usable as a type) as
    it is seen. Later declarations are not visible to earlier ones, exactly as
    for a nested struct field.
    """
    structs = {}
    enums = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.ClassDef):
            continue
        bases = stmt.bases
        base = bases[0].id if (len(bases) == 1 and isinstance(bases[0], ast.Name)) else None
        if base == STRUCT_BASE:
            _build_struct_layout(stmt, structs, enums)
        elif base == ENUM_BASE:
            _build_enum_layout(stmt, structs, enums)
        else:
            raise NotImplementedError(
                f"LITHON-E0126: class '{stmt.name}' must derive from Struct or Enum, e.g. "
                f"`class {stmt.name}(Struct):` or `class {stmt.name}(Enum):` -- other bases "
                f"and inheritance are not supported (4.6/4.7)")
    return structs, enums


def scalar_cell_bytes(kind, width):
    """Bytes a scalar cell occupies in a frame: int[N] and float[N] are N/8
    bytes, bool is one. This is the stride a pointer to such a cell adds and
    the length a valueof of it reads."""
    if kind == "bool":
        return 1
    # 4.4, second half. A struct's byte size IS its pointee size: the pointer
    # names the blob, so `_p + 1` advances by the whole struct rather than by
    # any field. The size comes from the struct's own layout, which the IR
    # carries as ptr[struct[N]].
    if kind == "struct":
        if width is None or width <= 0:
            raise NotImplementedError(
                f"LITHON-E0113: a pointer to a struct needs its byte size, which comes "
                f"from the struct's declaration (4.4)")
        return width
    if kind in ("int", "float"):
        if width is None or width <= 0:
            raise NotImplementedError(
                f"LITHON-E0113: {kind} needs a stated width to be pointed at, e.g. {kind}[64] (4.4)")
        return width // 8
    raise NotImplementedError(
        f"LITHON-E0113: only a scalar cell can be pointed at, got {kind} (4.4)")


BINARY_OPS = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
    ast.Mod: "mod",
}

# Bitwise/shift, kept out of BINARY_OPS on purpose. BINARY_OPS is the
# promoting table: any int/float mix in it becomes a float. These operators are
# integer-only and are a type error on floats, so sharing the table would make
# `2.5 & 1` silently legal. The `b` prefixes keep them clear of the logical
# `and`/`or` IR opcodes, which are different operations entirely.
BITWISE_OPS = {
    ast.LShift: "shl",
    ast.RShift: "shr",
    ast.BitAnd: "band",
    ast.BitOr: "bor",
    ast.BitXor: "bxor",
}


class IRBuilder:
    def __init__(self, fn_rename=None, structs=None, enums=None):
        self.fn_rename = fn_rename or {}
        self.structs = structs or {}
        self.enums = enums or {}
        self.reg_counter = 0
        self.block_counter = 0
        self.ifexpr_counter = 0
        self.blocks = []
        self.current = None
        self.terminated = False
        self.loop_stack = []  # list of (continue_target, break_target)
        self.dict_names = set()
        self.var_types = {}
        self.ptr_types = {}
        self.ptr_names = set()
        self.declared = {}
        # 4.6. Local variables that hold a struct, mapped to their struct TYPE
        # name (a key into self.structs). A struct variable is not a value, so
        # the only things that can be done to it are field reads and writes, and
        # this is what tells `.field` apart from an attribute this frontend does
        # not support.
        self.struct_vars = {}
        # 4.7. Local variables that hold an enum, mapped to their enum TYPE name
        # (a key into self.enums). An enum is storage too: it is constructed into
        # and read back through `match`, never loaded as a value.
        self.enum_vars = {}
        # 4.9. Method-inlining state. `method_return_stack` holds one
        # (result_temp_or_None, return_label) per method being inlined, so a
        # `return` in a method jumps to the end of that inline rather than to the
        # enclosing function. `inline_active` names the (type, method) pairs on
        # the inline stack, which is what catches recursion. `inline_counter`
        # gives each call site a unique prefix so its generated locals cannot
        # collide with the caller's.
        self.method_return_stack = []
        self.inline_active = []
        self.inline_counter = 0
        # 6.1. Names of loop counters this function has generated. A `zip` for
        # header needs a counter the programmer did not write (unlike
        # `enumerate`, whose first target IS the index), and that name has to
        # be guaranteed not to collide with anything the programmer did write --
        # see _fresh_loop_counter.
        self.loop_counters = set()
        # 4.9. Reachability. Every block is entered either by being the entry
        # block or by a `jump`/`branch` to its label. Method inlining needs this:
        # when every arm of a method's body returns, the merge block that
        # follows is dead, and jumping from it to the inline's end would add a
        # predecessor that never assigned the result. `entry_label` is the first
        # block started and `reachable_labels` collects every branch target.
        self.entry_label = None
        self.reachable_labels = set()

    def new_reg(self):
        r = self.reg_counter
        self.reg_counter += 1
        return f"%{r}"

    def reserve_label(self):
        label = f"block{self.block_counter}"
        self.block_counter += 1
        return label

    def start_block(self, label):
        b = Block(label)
        self.blocks.append(b)
        self.current = b
        self.terminated = False
        if self.entry_label is None:
            self.entry_label = label
        return b

    def block_is_reachable(self, label):
        return label == self.entry_label or label in self.reachable_labels

    def note_terminator_targets(self, stripped):
        """Records the label(s) a `jump`/`branch` line targets. Every terminator
        goes through here (or `emit`, which calls it) so reachability stays
        complete even when a line is dropped straight into a block."""
        if stripped.startswith("jump "):
            self.reachable_labels.add(stripped[len("jump "):])
        elif stripped.startswith("branch "):
            for label in stripped[len("branch "):].split(",")[1:]:
                self.reachable_labels.add(label.strip())

    def emit(self, line):
        # A block ends at its first terminator. Source after `break`,
        # `continue`, or `return` is unreachable, and an `if`/loop that appends
        # its own trailing jump onto a body that already terminated would give
        # the block two terminators. build_cfg reads only the last one, so the
        # CFG would disagree with the code the emitter actually lays down.
        if self.terminated:
            return
        self.current.lines.append(f"    {line}")
        stripped = line.strip()
        self.note_terminator_targets(stripped)
        if stripped.startswith("jump ") or stripped.startswith("branch ") \
                or stripped == "return" or stripped.startswith("return "):
            self.terminated = True

    def literal_int(self, node):
        """The integer value of a literal expression node, or None. Used by
        pointer arithmetic to consume an offset from source text before the IR
        sees a raw count. A negated literal counts (Python parses `-1` as
        UnaryOp(USub, 1)); any other shape is not a literal."""
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and \
                isinstance(node.operand, ast.Constant) and \
                isinstance(node.operand.value, int) and \
                not isinstance(node.operand.value, bool):
            return -node.operand.value
        return None

    def _input_prompt_literal(self, call):
        """The prompt string of an input() call, or "" when there is none.

        Only a string literal prompt is accepted: input() has no value of its
        own in this slice (that would need str[N]), so the prompt must be
        knowable at compile time to be hex-encoded into the IR."""
        if len(call.args) == 0:
            return ""
        if len(call.args) == 1 and isinstance(call.args[0], ast.Constant) \
                and isinstance(call.args[0].value, str):
            return call.args[0].value
        raise NotImplementedError(
            "LITHON-E0115: input() takes an optional string literal prompt, e.g. "
            'input("Enter x: ")')

    def _build_membership(self, positive, value_node, container_node):
        """`x in xs` (or `not in`) over a fixed-capacity list.

        6.1. The OR of `x == xs[k]` for k in 0..N-1. Capacity is a compile-time
        constant, so this is straight-line code and there is no scan at run time;
        the cost is the same OR-chain a hand-written loop would emit.
        """
        if not isinstance(container_node, ast.Name):
            raise NotImplementedError(
                "LITHON-E0114: `in` needs a plain list or tuple variable on the right, "
                "e.g. `x in xs` -- a container has no value to scan yet")
        cap = self._container_capacity(container_node.id)
        if cap is None:
            raise NotImplementedError(
                f"LITHON-E0114: `in` needs a list or tuple declared with its capacity, "
                f"e.g. `{container_node.id}: list[int[64], 8]`")
        # The element comparison is `eq`, and `eq` does not accept bools
        # anywhere in the language today -- `a == b` on two bools is refused by
        # the runtime too. Rather than let `in` fail later with "comparison on a
        # non-numeric value" from inside the interpreter, say so here, where the
        # source names the list.
        elem_kind = self.declared[container_node.id][2]
        if elem_kind not in ("int", "float"):
            raise NotImplementedError(
                f"LITHON-E0114: `in` over a list of {elem_kind} is not supported -- it "
                f"lowers to `==` over the elements, and equality does not take bools "
                f"yet (the same refusal as `a == b` on two bools)")
        value = self.build_expr(value_node)
        name = container_node.id
        false_r = self.new_reg()
        self.emit(f"{false_r} = const_bool 0")
        acc = false_r
        for k in range(cap):
            idx = self.new_reg()
            self.emit(f"{idx} = const_i64 {k}")
            el = self.new_reg()
            self.emit(f"{el} = Index {name}, {idx}")
            cmp_r = self.new_reg()
            self.emit(f"{cmp_r} = eq {value}, {el}")
            nxt = self.new_reg()
            self.emit(f"{nxt} = or {acc}, {cmp_r}")
            acc = nxt
        if positive:
            return acc
        r = self.new_reg()
        self.emit(f"{r} = not {acc}")
        return r

    def _emit_select(self, cond_reg, then_reg, else_reg, kind, width):
        """`then_reg if cond else else_reg`, as registers rather than AST.

        6.1's min/max need a select per element. The IR text format has no phi
        opcode, so this is the same diamond the if-expression lowering builds
        (see build_expr's ast.IfExp arm): both arms store one temporary and the
        merge block loads it -- which the dominance-frontier analysis sees as a
        single Phi at the join.
        """
        temp = f"{IF_EXPR_TEMP}{self.ifexpr_counter}"
        self.ifexpr_counter += 1
        then_label = self.reserve_label()
        else_label = self.reserve_label()
        merge_label = self.reserve_label()

        self.emit(f"branch {cond_reg}, {then_label}, {else_label}")

        self.start_block(then_label)
        self.emit(f"store {temp}, {then_reg}")
        self.emit(f"jump {merge_label}")

        self.start_block(else_label)
        self.emit(f"store {temp}, {else_reg}")
        self.emit(f"jump {merge_label}")

        self.start_block(merge_label)
        r = self.new_reg()
        self.emit(f"{r} = load {temp}{render_type_suffix(kind, width)}")
        return r

    def _reduce_list_arg(self, fname, node):
        """The single container argument of sum/min/max/any/all, as (name, decl).

        Every one of them is a rewrite over the compile-time capacity N, so they
        all take a plain list/tuple variable -- the same shape `len()` and
        `enumerate` take, because there is no list value to pass around yet.
        """
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
            raise NotImplementedError(
                f"LITHON-E0115: {fname}() takes exactly one list or tuple variable, "
                f"e.g. {fname}(xs)")
        name = node.args[0].id
        cap = self._container_capacity(name)
        if cap is None:
            raise NotImplementedError(
                f"LITHON-E0115: {fname}({name}) needs a list or tuple declared with its "
                f"capacity, e.g. `{name}: list[int[64], 8]`")
        return name, self.declared[name]

    def build_expr(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            r = self.new_reg()
            self.emit(f"{r} = const_bool {1 if node.value else 0}")
            return r

        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            r = self.new_reg()
            self.emit(f"{r} = const_f64 {node.value}")
            return r

        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            r = self.new_reg()
            self.emit(f"{r} = const_i64 {node.value}")
            return r

        if isinstance(node, ast.Name):
            # 4.6. A struct variable is storage, not a value: it has no value to
            # load and nothing that could consume one. Reading it as a whole
            # (print(s), s + 1, passing it to a function) is refused here, where
            # the source names it, rather than emitting a `load` that would read
            # the first field as though it were the struct.
            if node.id in self.struct_vars:
                raise NotImplementedError(
                    f"LITHON-E0128: '{node.id}' is a struct and has no value -- read or write "
                    f"one of its fields, e.g. {node.id}.<field> (4.6)")
            # 4.7. An enum is storage too, and the only read is a `match`. Naming
            # it in a value position would otherwise emit a load of its first
            # byte (the tag) as though the enum were that int.
            if node.id in self.enum_vars:
                raise NotImplementedError(
                    f"LITHON-E0131: '{node.id}' is an enum and has no value -- read it with a "
                    f"`match` statement (4.7)")
            r = self.new_reg()
            self.emit(f"{r} = load {node.id}")
            # 4.4. A load of a pointer variable brings the pointer's pointee
            # with it, so later `_p + 1` knows the cell size it must scale by.
            if node.id in self.ptr_names:
                self.ptr_types[r] = self.var_types[node.id]
            return r

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            op_map = BITWISE_OPS if op_type in BITWISE_OPS else BINARY_OPS
            if op_type not in op_map:
                raise NotImplementedError(f"LITHON-E0114: operator {op_type.__name__} not supported yet")
            if op_type in (ast.Add, ast.Sub):
                # 4.4. Pointer arithmetic. If either side is a pointer register
                # the other side must be a literal ELEMENT count, because a byte
                # address only moves in multiples of the pointee's cell size.
                # The offset is scaled here -- `_p + 3` becomes `add _p, 24` for
                # an int[64] -- because the IR registers carry no pointer type
                # for the typechecker or codegen to scale later. The scale is
                # decided from source text, not from the built value, so the
                # literal is consumed here rather than emitted as a raw count.
                right_lit = self.literal_int(node.right)
                if right_lit is not None:
                    # `_p + K` or `_p - K`, the pointer on the left.
                    left = self.build_expr(node.left)
                    if left in self.ptr_types:
                        k, w = self.ptr_types[left]
                        off = self.new_reg()
                        self.emit(f"{off} = const_i64 {right_lit * scalar_cell_bytes(k, w)}")
                        r = self.new_reg()
                        self.emit(f"{r} = {op_map[op_type]} {left}, {off}")
                        self.ptr_types[r] = (k, w)
                        return r
                left_lit = self.literal_int(node.left)
                if left_lit is not None:
                    # `K + _p` / `K - _p`, the pointer on the right.
                    right = self.build_expr(node.right)
                    if right in self.ptr_types:
                        k, w = self.ptr_types[right]
                        off = self.new_reg()
                        self.emit(f"{off} = const_i64 {left_lit * scalar_cell_bytes(k, w)}")
                        r = self.new_reg()
                        self.emit(f"{r} = {op_map[op_type]} {off}, {right}")
                        self.ptr_types[r] = (k, w)
                        return r
                left = self.build_expr(node.left)
                right = self.build_expr(node.right)
                if left in self.ptr_types or right in self.ptr_types:
                    raise NotImplementedError(
                        "LITHON-E0125: a pointer offset must be a literal integer, e.g. `_p + 1` (4.4)")
                r = self.new_reg()
                self.emit(f"{r} = {op_map[op_type]} {left}, {right}")
                return r
            left = self.build_expr(node.left)
            right = self.build_expr(node.right)
            r = self.new_reg()
            self.emit(f"{r} = {op_map[op_type]} {left}, {right}")
            return r

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise NotImplementedError("LITHON-E0114: chained comparisons (a < b < c) not supported yet")
            op_type = type(node.ops[0])
            # 6.1. `x in xs` / `x not in xs` on a list. Like every other helper
            # here it is a rewrite over the compile-time capacity: the OR of
            # `x == xs[k]` for k in 0..N-1, and `not in` is that OR negated.
            # There is no membership opcode and no scan at run time.
            if op_type in (ast.In, ast.NotIn):
                return self._build_membership(
                    op_type is ast.In,
                    node.left,
                    node.comparators[0])
            left = self.build_expr(node.left)
            right = self.build_expr(node.comparators[0])
            op_map = {ast.Lt: "lt", ast.Gt: "gt", ast.Eq: "eq"}
            if op_type not in op_map:
                raise NotImplementedError(f"LITHON-E0114: comparison {op_type.__name__} not supported yet")
            r = self.new_reg()
            self.emit(f"{r} = {op_map[op_type]} {left}, {right}")
            return r

        if isinstance(node, ast.BoolOp):
            if len(node.values) != 2:
                raise NotImplementedError("LITHON-E0114: and/or with exactly two operands supported for now")
            left = self.build_expr(node.values[0])
            right = self.build_expr(node.values[1])
            op_name = "and" if isinstance(node.op, ast.And) else "or"
            r = self.new_reg()
            self.emit(f"{r} = {op_name} {left}, {right}")
            return r

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand = self.build_expr(node.operand)
            r = self.new_reg()
            self.emit(f"{r} = not {operand}")
            return r

        # Unary minus. There is no Neg opcode in the IR, so a negated
        # *literal* is folded into its constant -- which is the overwhelmingly
        # common case, since `-1` and `-2.5` reach here as UnaryOp(USub,
        # Constant) rather than as a negative literal token. Anything else
        # becomes `0 - x`, which needs no new opcode and lowers to a Sub that
        # the existing int and float paths already handle (the 0 is promoted
        # to a double automatically when x is a float).
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = node.operand
            if isinstance(inner, ast.Constant) and isinstance(inner.value, bool) is False:
                if isinstance(inner.value, int):
                    r = self.new_reg()
                    self.emit(f"{r} = const_i64 {-inner.value}")
                    return r
                if isinstance(inner.value, float):
                    r = self.new_reg()
                    self.emit(f"{r} = const_f64 {-inner.value!r}")
                    return r
            operand = self.build_expr(inner)
            zero = self.new_reg()
            self.emit(f"{zero} = const_i64 0")
            r = self.new_reg()
            self.emit(f"{r} = sub {zero}, {operand}")
            return r

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                # 4.9. `recv.method(args)` -- a method call, inlined right here.
                # A receiver that is not a struct/enum place, or a method the
                # type does not have, is refused inside with E0135.
                r = self._inline_method_call(node)
                if r is None:
                    raise NotImplementedError(
                        f"LITHON-E0135: method '{node.func.attr}()' returns no value, so it "
                        f"cannot be used as a value (4.9)")
                return r
            if not isinstance(node.func, ast.Name):
                raise NotImplementedError("LITHON-E0115: only direct name calls are supported")
            if node.func.id == "print":
                raise NotImplementedError("LITHON-E0115: print() is a statement in this slice, not an expression")
            if node.func.id in ("sum", "min", "max", "any", "all"):
                return self._build_reduction(node.func.id, node)
            if node.func.id == "abs":
                # 6.1. Scalar, unlike the others: abs() is a question about one
                # value, not a walk over N. It lowers to the same diamond as an
                # if-expression, so `abs(v)` is `-v if v < 0 else v`.
                if len(node.args) != 1:
                    raise NotImplementedError(
                        "LITHON-E0115: abs() takes exactly one value, e.g. abs(x)")
                v = self.build_expr(node.args[0])
                zero = self.new_reg()
                self.emit(f"{zero} = const_i64 0")
                neg = self.new_reg()
                is_neg = self.new_reg()
                self.emit(f"{is_neg} = lt {v}, {zero}")
                # The negation is emitted unconditionally: wrapping negation of
                # the most negative int64 is itself, and it is discarded by the
                # select when the value was not negative. The branch is what
                # keeps the live value correct either way.
                self.emit(f"{neg} = wrapsub {zero}, {v}")
                return self._emit_select(is_neg, neg, v, "int", 64)
            if node.func.id in ("enumerate", "zip"):
                # 6.1. Header-only by construction, so say so here rather than
                # letting it fall through to a generic `call` that the type
                # checker rejects with "unknown function" -- which is true but
                # useless to someone who simply wrote the loop the Python way.
                raise NotImplementedError(
                    f"LITHON-E0115: {node.func.id}(...) is only usable in a for header, "
                    f"e.g. for a, b in {node.func.id}(xs, ys) -- it is a rewrite of a "
                    f"counted loop, not a value, so there is nothing to bind here (6.1)")
            if node.func.id == "len":
                # Op::Len names a container variable and returns the capacity.
                # It is not a call: there is no `len` function to emit a `call`
                # for, and a user function named `len` would otherwise collide.
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError("LITHON-E0115: len() takes exactly one variable")
                r = self.new_reg()
                self.emit(f"{r} = Len {node.args[0].id}")
                return r
            if node.func.id == "contains":
                # 4.3. Like len, this is not a call to a `contains` function. It
                # is a builtin that lowers to one opcode, so a user function of
                # that name would collide and is rejected by the checker instead.
                if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError(
                        "LITHON-E0115: contains() takes a dict variable and a key, e.g. contains(d, k)")
                key_reg = self.build_expr(node.args[1])
                r = self.new_reg()
                self.emit(f"{r} = DictContains {node.args[0].id}, {key_reg}")
                return r
            if node.func.id == "valueof":
                # 4.4. A pointer read. The argument builds to a register that
                # may be a freshly minted addressof, a load of a pointer, or
                # pointer arithmetic chained from either -- the register is
                # tagged with its pointee at every one of those points, so the
                # pointee is known here and rendered as the instruction's
                # suffix. That suffix is what names the width of the load.
                if len(node.args) != 1:
                    raise NotImplementedError(
                        "LITHON-E0116: valueof() takes exactly one pointer, e.g. valueof(_p) (4.4)")
                arg_reg = self.build_expr(node.args[0])
                if arg_reg not in self.ptr_types:
                    raise NotImplementedError(
                        "LITHON-E0116: valueof() needs a pointer, e.g. valueof(_p) (4.4)")
                k, w = self.ptr_types[arg_reg]
                r = self.new_reg()
                self.emit(f"{r} = valueof {arg_reg}{render_type_suffix(k, w)}")
                return r
            if node.func.id == "addressof":
                # 4.4. A cell address. The target must be a plain scalar
                # variable whose size is known -- annotated, since an address on
                # an unannotated variable could not say how big the cell is and
                # the pointer would not know its own stride.
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError(
                        "LITHON-E0116: addressof() takes exactly one plain variable, e.g. "
                        "addressof(x) (4.4)")
                name = node.args[0].id
                if name in self.struct_vars:
                    # 4.4, second half. A struct is storage, and its bytes ARE
                    # its addressable run -- there is no separate box to point
                    # into. So addressof(p) is the blob's base, typed
                    # ptr[struct], and pointer arithmetic on it advances by the
                    # whole struct. This is also what makes ptr[int[8]] into
                    # struct memory real (5.2's send/recv buffers) with no
                    # special syscall operand.
                    layout = self.structs[self.struct_vars[name]]
                    r = self.new_reg()
                    self.emit(f"{r} = addressof {name}")
                    self.ptr_types[r] = ("struct", layout.size)
                    return r
                if name not in self.var_types:
                    raise NotImplementedError(
                        f"LITHON-E0116: addressof('{name}') needs the size of '{name}', so "
                        f"annotate it first, e.g. `{name}: int[64] = ...` (4.4)")
                r = self.new_reg()
                self.emit(f"{r} = addressof {name}")
                self.ptr_types[r] = self.var_types[name]
                return r
            if node.func.id in ("wrap_add", "wrap_sub", "wrap_mul"):
                # E0303 opt-out. Compiler-recognized names like addressof/
                # valueof: the call lowers to one wrapping IR op so the
                # typechecker can enforce int[64]-only operands and the JIT can
                # emit the same arithmetic WITHOUT the overflow trap. A user
                # function of this name is shadowed, exactly like len/contains.
                if len(node.args) != 2:
                    raise NotImplementedError(
                        f"LITHON-E0116: {node.func.id}() takes exactly two int[64] operands, "
                        f"e.g. {node.func.id}(a, b) (E0303)")
                op = {"wrap_add": "wrapadd", "wrap_sub": "wrapsub",
                      "wrap_mul": "wrapmul"}[node.func.id]
                left = self.build_expr(node.args[0])
                right = self.build_expr(node.args[1])
                if left in self.ptr_types or right in self.ptr_types:
                    raise NotImplementedError(
                        f"LITHON-E0116: {node.func.id}() is integer-only -- pointer arithmetic "
                        f"is not wrapping arithmetic (E0303, int[64] operands only)")
                r = self.new_reg()
                self.emit(f"{r} = {op} {left}, {right}")
                return r
            if node.func.id in ("int", "float"):
                # Hardening item 5. Explicit conversions and terminal input
                # (V1_SPEC 0.6.11). Two-argument form with an explicit width:
                #   float(64, v)              -- int[8|16|32] or float[64]
                #   int(w, v)                 -- float[64] -> int[w]
                #   int(w, input("prompt"))   -- read int[w] from stdin
                # Like len/contains/addressof, these builtins shadow a user
                # function of the same name rather than emitting a `call`.
                if len(node.args) != 2:
                    raise NotImplementedError(
                        f"LITHON-E0115: {node.func.id}() takes a bit width and a value, e.g. "
                        f"{node.func.id}(64, x)")
                width = self.literal_int(node.args[0])
                if width is None:
                    raise NotImplementedError(
                        f"LITHON-E0115: {node.func.id}() needs a literal bit width, e.g. "
                        f"{node.func.id}(64, x)")
                if node.func.id == "float":
                    arg_reg = self.build_expr(node.args[1])
                    r = self.new_reg()
                    self.emit(f"{r} = tofloat {arg_reg}"
                              f"{render_type_suffix('float', width)}")
                    return r
                src = node.args[1]
                if isinstance(src, ast.Call) and isinstance(src.func, ast.Name) \
                        and src.func.id == "input":
                    prompt = self._input_prompt_literal(src)
                    # Hex-encode so any prompt character (comma, space,
                    # bracket, newline) survives the comma-split text IR. The
                    # "-" sentinel means no prompt.
                    hex_prompt = prompt.encode("utf-8").hex() if prompt else "-"
                    r = self.new_reg()
                    self.emit(f"{r} = inputint {hex_prompt}"
                              f"{render_type_suffix('int', width)}")
                    return r
                arg_reg = self.build_expr(src)
                r = self.new_reg()
                self.emit(f"{r} = toint {arg_reg}"
                          f"{render_type_suffix('int', width)}")
                return r
            if node.func.id == "syscall":
                # Phase III. The raw syscall primitive. Like addressof/
                # valueof/len, this is a compiler-recognized name, not a call
                # to a function: it lowers to one `syscall` op whose first
                # operand is the syscall number and whose remaining operands
                # (up to six) are the arguments. The signature is
                # deliberately unchecked -- `syscall(n, a1..a6) -> int[64]`,
                # a negative result is -errno on Linux -- so there is no
                # fallible wrapper to unwrap here. Arguments are integers in
                # this slice (the typechecker rejects pointers, since the
                # interpreter's synthetic addresses are not real memory). A
                # user function named `syscall` would collide and is rejected
                # by the checker.
                if not (1 <= len(node.args) <= 7):
                    raise NotImplementedError(
                        "LITHON-E0115: syscall() takes a syscall number and up to six "
                        "integer arguments, e.g. syscall(102) (Phase III)")
                arg_regs = [self.build_expr(a) for a in node.args]
                r = self.new_reg()
                self.emit(f"{r} = syscall {', '.join(arg_regs)}")
                return r
            arg_regs = [self.build_expr(a) for a in node.args]
            callee = self.fn_rename.get(node.func.id, node.func.id)
            r = self.new_reg()
            if arg_regs:
                self.emit(f"{r} = call {callee}, {', '.join(arg_regs)}")
            else:
                self.emit(f"{r} = call {callee}")
            return r

        # `xs[i]`. Op::Index names a container VARIABLE rather than taking a
        # ValueId, because the address comes from the variable's slot run. That
        # is also why there is no list value to subscript -- see the note on
        # assignment below.
        if isinstance(node, ast.Subscript):
            if not isinstance(node.value, ast.Name):
                raise NotImplementedError(
                    "LITHON-E0117: only a plain variable can be indexed, e.g. xs[i]; there is no "
                    "value that denotes a container yet")
            idx = self.build_expr(node.slice)
            r = self.new_reg()
            # 4.3. A dict subscript is a hash and a probe, not an address
            # computation, so it is a different opcode. The variable's kind is
            # remembered at its declaration because this is the only place that
            # knows which one of the two it is, and guessing would silently pick
            # the wrong address arithmetic.
            op = "DictIndex" if node.value.id in self.dict_names else "Index"
            self.emit(f"{r} = {op} {node.value.id}, {idx}")
            return r

        if isinstance(node, ast.IfExp):
            # `a if cond else b` as an expression. The IR text format has no
            # phi opcode, so the value is given a temporary variable that both
            # arms store and the merge block loads -- exactly the diamond that
            # the dominance-frontier analysis sees as one Phi at the join.
            # Nothing is forced through a stack slot it did not already need:
            # the same shape is what a source-level merge would produce.
            cond_reg = self.build_expr(node.test)
            temp = f"{IF_EXPR_TEMP}{self.ifexpr_counter}"
            self.ifexpr_counter += 1

            then_label = self.reserve_label()
            else_label = self.reserve_label()
            merge_label = self.reserve_label()

            self.emit(f"branch {cond_reg}, {then_label}, {else_label}")

            self.start_block(then_label)
            then_reg = self.build_expr(node.body)
            self.emit(f"store {temp}, {then_reg}")
            self.emit(f"jump {merge_label}")

            self.start_block(else_label)
            else_reg = self.build_expr(node.orelse)
            self.emit(f"store {temp}, {else_reg}")
            self.emit(f"jump {merge_label}")

            self.start_block(merge_label)
            r = self.new_reg()
            self.emit(f"{r} = load {temp}")
            return r

        # 4.6. `s.field` and nested `s.inner.field`. The byte offset is resolved
        # from the struct layout and baked into the instruction, so the tiers see
        # one leaf field read rather than a chain of intermediate structs (which
        # have no values to be intermediate). A struct-typed field therefore has
        # no standalone value, only its own fields do.
        if isinstance(node, ast.Attribute):
            var, offset, kind, width = self._resolve_struct_field(node)
            r = self.new_reg()
            self.emit(f"{r} = fieldload {var}, {offset}{render_type_suffix(kind, width)}")
            return r

        raise NotImplementedError(f"LITHON-E0114: expression node {type(node).__name__} not supported yet")

    def _resolve_struct_field(self, node):
        """Resolves an attribute chain `a.b.c...` to (variable, byte_offset,
        leaf_kind, leaf_width), or raises the E0128 refusal that explains why it
        is not a field access.

        The chain must bottom out at a plain struct VARIABLE, and every step but
        the last must be a struct-typed field (so the offset walk is meaningful).
        The leaf must be a scalar: a struct-typed leaf has no value, which is the
        same rule that refuses a bare struct name.
        """
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            raise NotImplementedError(
                "LITHON-E0128: field access must start from a plain struct variable (4.6)")
        var = cur.id
        if var not in self.struct_vars:
            raise NotImplementedError(
                f"LITHON-E0128: '{var}' is not a struct, so it has no fields (4.6)")
        parts.reverse()

        layout = self.structs[self.struct_vars[var]]
        offset = 0
        kind = width = None
        for i, field_name in enumerate(parts):
            field = layout.fields.get(field_name)
            if field is None:
                if field_name in layout.methods:
                    raise NotImplementedError(
                        f"LITHON-E0135: '{field_name}' is a method of struct '{layout.name}', not "
                        f"a field -- call it instead, e.g. `{var}.{field_name}(...)` (4.9)")
                raise NotImplementedError(
                    f"LITHON-E0128: struct '{layout.name}' has no field '{field_name}' (4.6)")
            offset += field["offset"]
            last = (i == len(parts) - 1)
            if field["kind"] == "struct":
                if last:
                    raise NotImplementedError(
                        f"LITHON-E0128: '{field_name}' is a struct field with no value -- read "
                        f"one of ITS fields, e.g. .{field_name}.<field> (4.6)")
                layout = self.structs[field["struct_name"]]
                continue
            if field["kind"] == "enum":
                raise NotImplementedError(
                    f"LITHON-E0131: '{field_name}' is an enum field and has no value -- "
                    f"construct it into `{var}.{field_name}` or read it with a `match` (4.7)")
            if not last:
                raise NotImplementedError(
                    f"LITHON-E0128: '{field_name}' is a {field['kind']}, not a struct, so it "
                    f"has no fields (4.6)")
            kind, width = field["kind"], field["width"]
        return var, offset, kind, width

    # ---- 4.9 methods -------------------------------------------------------
    #
    # A method is not a value and not a separate IR function: a call to
    # `recv.name(args)` INLINES the method body at the call site. That is forced
    # by the storage model -- `self.field` has to resolve to the caller's
    # variable (fieldload/fieldstore name a variable, not a register), and a
    # struct cannot be passed to a function anyway (E0124). Inlining is what lets
    # a method read AND write the receiver's fields, run loops and branches, and
    # call other methods. It cannot recurse, because the body would never stop
    # expanding; recursion is refused instead.

    def _resolve_receiver_type(self, node):
        """The (kind, type_name) of the receiver place `node`, or None when it
        is not a struct or enum variable/field.

        A receiver is a struct/enum variable or an attribute chain that ends at
        a struct- or enum-typed field (`a.inner`, `s.e`). The type name indexes
        self.structs or self.enums, whose `methods` map holds the methods.
        """
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        var = cur.id
        if not parts:
            if var in self.struct_vars:
                return "struct", self.struct_vars[var]
            if var in self.enum_vars:
                return "enum", self.enum_vars[var]
            return None
        if var not in self.struct_vars:
            return None
        parts.reverse()
        layout = self.structs[self.struct_vars[var]]
        for i, field_name in enumerate(parts):
            field = layout.fields.get(field_name)
            if field is None:
                return None
            last = (i == len(parts) - 1)
            if field["kind"] == "struct":
                if last:
                    return "struct", field["struct_name"]
                layout = self.structs[field["struct_name"]]
            elif field["kind"] == "enum":
                if last:
                    return "enum", field["enum_name"]
                return None
            else:
                return None
        return None

    def _known_names(self):
        """Every name bound anywhere in the current function.

        Same set _fresh_method_prefix scans, plus the loop counters: a
        `for i in range(...)` target is stored before it is read but is never
        declared, so it is in none of the other collections, and a generated
        name must not collide with it either.
        """
        return (set(self.var_types) | self.ptr_names | set(self.declared)
                | set(self.struct_vars) | set(self.enum_vars) | self.dict_names
                | self.loop_counters)

    def _fresh_loop_counter(self):
        """A scalar name for a compiler-generated loop counter.

        6.1 `zip` needs a counter of its own: the programmer names only the
        ELEMENTS (`for a, b in zip(xs, ys)`), so nothing in the source can
        serve as the index.

        The name cannot begin with `_`: 4.4 reserves that prefix for pointers
        and both the frontend (E0122) and the type checker reject an `_`-named
        non-pointer, so `__zip_i` would be refused -- the same reason
        _fresh_method_prefix cannot use an underscore either. Hence `zip_i`,
        with a counter suffix, skipping any name already in use.
        """
        known = self._known_names()
        while True:
            name = f"zip_i{self.inline_counter}"
            self.inline_counter += 1
            if name not in known:
                self.loop_counters.add(name)
                return name

    def _fresh_method_prefix(self):
        """A per-inline name prefix that no caller name starts with.

        Method locals are renamed `prefix + name` and the result temp is
        `prefix + "ret"`. A scalar name may not begin with `_` (E0122), so the
        prefix cannot either; the counter keeps successive call sites apart, and
        the scan guarantees a generated name can never collide with a caller
        name that happens to begin the same way.
        """
        while True:
            prefix = f"m{self.inline_counter}_"
            self.inline_counter += 1
            known = set(self.var_types) | self.ptr_names | set(self.declared) \
                | set(self.struct_vars) | set(self.enum_vars) | self.dict_names
            if not any(n.startswith(prefix) for n in known):
                return prefix

    def _inline_method_call(self, call):
        """Inlines `recv.method(args)` (or `E.V(...)`-free attribute call) and
        returns the result register, or None for a method that returns nothing.

        The receiver is a place and becomes `self` inside the body. Parameters
        are evaluated once, left to right, into freshly named locals declared
        with the method's own annotations; the method's own locals are renamed
        with the same per-call prefix, so the two scopes cannot collide. A `return`
        stores into a result local and jumps to the inline's end label.
        """
        recv_node = node = call.func.value
        resolved = self._resolve_receiver_type(node)
        if resolved is None:
            raise NotImplementedError(
                f"LITHON-E0135: cannot call '{call.func.attr}()' here -- a method is called on a "
                f"struct or enum variable, e.g. `p.{call.func.attr}(...)` (4.9)")
        kind, type_name = resolved
        layout = (self.structs if kind == "struct" else self.enums)[type_name]
        method_name = call.func.attr
        if method_name in (layout.fields if kind == "struct" else layout.variant_by_name):
            raise NotImplementedError(
                f"LITHON-E0135: '{method_name}' in {kind} '{type_name}' is a field, not a "
                f"method, so it cannot be called (4.9)")
        method = layout.methods.get(method_name)
        if method is None:
            raise NotImplementedError(
                f"LITHON-E0135: {kind} '{type_name}' has no method '{method_name}' (4.9)")
        params = method.args.args[1:]
        if call.keywords or any(isinstance(a, ast.Starred) for a in call.args):
            raise NotImplementedError(
                f"LITHON-E0135: method '{type_name}.{method_name}' takes positional arguments "
                f"only (4.9)")
        if len(call.args) != len(params):
            raise NotImplementedError(
                f"LITHON-E0135: method '{type_name}.{method_name}' takes {len(params)} "
                f"argument(s), got {len(call.args)} (4.9)")
        has_result = method.returns is not None

        token = (type_name, method_name)
        if token in self.inline_active:
            chain = " -> ".join(f"{t}.{m}" for t, m in self.inline_active + [token])
            raise NotImplementedError(
                f"LITHON-E0135: method call is recursive ({chain}); a method is inlined at the "
                f"call site and cannot call itself, directly or indirectly (4.9)")

        prefix = self._fresh_method_prefix()
        rename = {nm: prefix + nm for nm in _collect_method_locals(method.body)}
        for p in params:
            rename[p.arg] = prefix + p.arg
        body = _MethodSubst(rename, recv_node).visit(
            copy.deepcopy(ast.Module(body=method.body, type_ignores=[]))).body

        ret_label = self.reserve_label()
        result_temp = (prefix + "ret") if has_result else None
        saved_loop = self.loop_stack
        self.loop_stack = []
        self.method_return_stack.append((result_temp, ret_label))
        self.inline_active.append(token)
        try:
            if has_result:
                self.build_stmt(ast.AnnAssign(
                    target=ast.Name(id=result_temp, ctx=ast.Store()),
                    annotation=copy.deepcopy(method.returns), value=None, simple=1))
            for p, arg in zip(params, call.args):
                self.build_stmt(ast.AnnAssign(
                    target=ast.Name(id=rename[p.arg], ctx=ast.Store()),
                    annotation=copy.deepcopy(p.annotation), value=arg, simple=1))
            for stmt in body:
                self.build_stmt(stmt)
            # The body ends in whatever block the last statement left current.
            # Normally that block falls through to the result load, so it jumps
            # to the end label. But when every path returned, that block is a
            # dead merge (no branch reaches it) and jumping from it would give
            # the result load a predecessor that never assigned -- the result
            # would look unassigned. Withhold the jump; the dead block then has
            # no successors and contributes nothing to the load.
            if not self.terminated and self.block_is_reachable(self.current.label):
                self.emit(f"jump {ret_label}")
            self.start_block(ret_label)
        finally:
            self.method_return_stack.pop()
            self.inline_active.pop()
            self.loop_stack = saved_loop
        if not has_result:
            return None
        r = self.new_reg()
        self.emit(f"{r} = load {result_temp}")
        return r

    # ---- 4.7 enums ---------------------------------------------------------
    #
    # An enum is storage, exactly like a struct: `store e : struct[N]` reserves
    # the blob, a construction writes the tag and the active payload, and a
    # `match` reads the tag and (inside the chosen arm) the payload. Every one of
    # those lowers to the same fieldload/fieldstore at a byte offset the layout
    # decides, so neither execution tier learns what a variant is.

    def _resolve_enum_place(self, node):
        """Resolves an assignable/matching enum place to (base_var, byte_offset,
        enum_type), or None when `node` does not name one.

        A place is a plain enum variable or an attribute chain that ends at an
        enum-typed struct field (`s.e`, `s.inner.e`). The walk reuses the struct
        layout, so the byte offset is the same summed offset a leaf field read
        would use.
        """
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        var = cur.id
        if not parts:
            if var in self.enum_vars:
                return var, 0, self.enum_vars[var]
            return None
        if var not in self.struct_vars:
            return None
        parts.reverse()
        layout = self.structs[self.struct_vars[var]]
        offset = 0
        for i, field_name in enumerate(parts):
            field = layout.fields.get(field_name)
            if field is None:
                return None
            offset += field["offset"]
            last = (i == len(parts) - 1)
            if field["kind"] == "struct":
                if last:
                    return None
                layout = self.structs[field["struct_name"]]
                continue
            if field["kind"] == "enum":
                if not last:
                    return None
                return var, offset, field["enum_name"]
            return None
        return None

    def _parse_enum_constructor(self, value_node, place_enum):
        """Matches `E.V` / `E.V(...)` against the enum named `place_enum` and
        returns (variant, call_node_or_None)."""
        if isinstance(value_node, ast.Attribute):
            qual, variant_name, call = value_node.value, value_node.attr, None
        elif isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Attribute):
            qual = value_node.func.value
            variant_name = value_node.func.attr
            call = value_node
        else:
            raise NotImplementedError(
                f"LITHON-E0130: a value of enum '{place_enum}' is built by naming one of its "
                f"variants, e.g. {place_enum}.V or {place_enum}.V(value) (4.7)")
        if not isinstance(qual, ast.Name) or qual.id != place_enum:
            raise NotImplementedError(
                f"LITHON-E0130: a value of enum '{place_enum}' is built from one of its own "
                f"variants, e.g. {place_enum}.V(...) (4.7)")
        layout = self.enums[place_enum]
        variant = layout.variant_by_name.get(variant_name)
        if variant is None:
            raise NotImplementedError(
                f"LITHON-E0130: enum '{place_enum}' has no variant '{variant_name}' (4.7)")
        return variant, call

    def _build_enum_construction(self, var, base_off, enum_name, value_node):
        """Writes a construction into the enum blob `var` at `base_off`: the tag
        first, then the active payload's fields, at fixed offsets."""
        layout = self.enums[enum_name]
        variant, call = self._parse_enum_constructor(value_node, enum_name)
        tag_reg = self.new_reg()
        self.emit(f"{tag_reg} = const_i64 {variant['tag']}")
        self.emit(f"fieldstore {var}, {base_off}, {tag_reg}"
                  f"{render_type_suffix('int', layout.tag_width)}")
        payload = variant["payload"]
        if payload is None:
            if call is not None and (call.args or call.keywords):
                raise NotImplementedError(
                    f"LITHON-E0130: variant '{enum_name}.{variant['name']}' is a unit variant "
                    f"and takes no payload (4.7)")
            return
        if payload["kind"] in ("int", "float", "bool"):
            if call is None:
                raise NotImplementedError(
                    f"LITHON-E0130: variant '{enum_name}.{variant['name']}' carries a payload; "
                    f"write {enum_name}.{variant['name']}(value) (4.7)")
            if call.keywords or len(call.args) != 1:
                raise NotImplementedError(
                    f"LITHON-E0130: variant '{enum_name}.{variant['name']}' takes exactly one "
                    f"payload value (4.7)")
            val_reg = self.build_expr(call.args[0])
            off = base_off + layout.payload_offset
            self.emit(f"fieldstore {var}, {off}, {val_reg}"
                      f"{render_type_suffix(payload['kind'], payload['width'])}")
            return
        # struct payload: set every field, by name or in declaration order.
        if call is None:
            raise NotImplementedError(
                f"LITHON-E0130: variant '{enum_name}.{variant['name']}' carries a struct "
                f"payload; write {enum_name}.{variant['name']}(field=value, ...) (4.7)")
        fields = self.structs[payload["struct_name"]].fields
        if call.args and call.keywords:
            raise NotImplementedError(
                f"LITHON-E0130: variant '{enum_name}.{variant['name']}' takes its fields either "
                f"all by name or all in order, not both (4.7)")
        supplied = {}
        if call.args:
            if len(call.args) != len(fields):
                raise NotImplementedError(
                    f"LITHON-E0130: variant '{enum_name}.{variant['name']}' needs "
                    f"{len(fields)} payload field(s), got {len(call.args)} (4.7)")
            for field_name, arg in zip(fields.keys(), call.args):
                supplied[field_name] = arg
        else:
            for kw in call.keywords:
                if kw.arg is None or kw.arg not in fields:
                    raise NotImplementedError(
                        f"LITHON-E0130: struct payload '{payload['struct_name']}' has no field "
                        f"'{kw.arg}' (4.7)")
                if kw.arg in supplied:
                    raise NotImplementedError(
                        f"LITHON-E0130: field '{kw.arg}' is set twice in this construction (4.7)")
                supplied[kw.arg] = kw.value
            missing = [fn for fn in fields if fn not in supplied]
            if missing:
                raise NotImplementedError(
                    f"LITHON-E0130: construction of variant '{enum_name}.{variant['name']}' is "
                    f"missing field(s) {', '.join(missing)} (4.7)")
        for field_name, field in fields.items():
            val_reg = self.build_expr(supplied[field_name])
            off = base_off + layout.payload_offset + field["offset"]
            self.emit(f"fieldstore {var}, {off}, {val_reg}"
                      f"{render_type_suffix(field['kind'], field['width'])}")

    def _parse_enum_pattern(self, pattern, enum_name):
        """Parses one `case` pattern against enum `enum_name`, returning
        (variant_name, binding). binding is None for a unit variant, a name for a
        scalar payload, or a {field: name} map for a struct payload."""
        layout = self.enums[enum_name]
        if isinstance(pattern, ast.MatchValue):
            value = pattern.value
            if not (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)) \
                    or value.value.id != enum_name:
                raise NotImplementedError(
                    f"LITHON-E0131: a unit variant arm is written `{enum_name}.V` (4.7)")
            variant = layout.variant_by_name.get(value.attr)
            if variant is None:
                raise NotImplementedError(
                    f"LITHON-E0131: enum '{enum_name}' has no variant '{value.attr}' (4.7)")
            if variant["payload"] is not None:
                raise NotImplementedError(
                    f"LITHON-E0131: variant '{enum_name}.{value.attr}' carries a payload -- bind "
                    f"it with {enum_name}.{value.attr}(...) (4.7)")
            return value.attr, None
        if isinstance(pattern, ast.MatchClass):
            cls = pattern.cls
            if not (isinstance(cls, ast.Attribute) and isinstance(cls.value, ast.Name)) \
                    or cls.value.id != enum_name:
                raise NotImplementedError(
                    f"LITHON-E0131: a variant arm is written `{enum_name}.V(...)` (4.7)")
            variant = layout.variant_by_name.get(cls.attr)
            if variant is None:
                raise NotImplementedError(
                    f"LITHON-E0131: enum '{enum_name}' has no variant '{cls.attr}' (4.7)")
            payload = variant["payload"]
            if payload is None:
                if pattern.patterns or pattern.kwd_attrs:
                    raise NotImplementedError(
                        f"LITHON-E0131: unit variant '{enum_name}.{cls.attr}' takes no payload "
                        f"binding (4.7)")
                return cls.attr, None
            if payload["kind"] in ("int", "float", "bool"):
                if pattern.kwd_attrs or len(pattern.patterns) != 1:
                    raise NotImplementedError(
                        f"LITHON-E0131: variant '{enum_name}.{cls.attr}' binds its payload with "
                        f"exactly one name, e.g. {enum_name}.{cls.attr}(x) (4.7)")
                sub = pattern.patterns[0]
                if not (isinstance(sub, ast.MatchAs) and sub.name and sub.pattern is None):
                    raise NotImplementedError(
                        f"LITHON-E0131: variant '{enum_name}.{cls.attr}' payload must be bound to "
                        f"a plain name (4.7)")
                return cls.attr, sub.name
            # struct payload
            if pattern.patterns:
                raise NotImplementedError(
                    f"LITHON-E0131: struct payload '{payload['struct_name']}' is bound by field "
                    f"name, e.g. {enum_name}.{cls.attr}(field=name, ...) (4.7)")
            fields = self.structs[payload["struct_name"]].fields
            binding = {}
            for attr, sub in zip(pattern.kwd_attrs, pattern.kwd_patterns):
                if attr not in fields:
                    raise NotImplementedError(
                        f"LITHON-E0131: struct payload '{payload['struct_name']}' has no field "
                        f"'{attr}' (4.7)")
                if attr in binding:
                    raise NotImplementedError(
                        f"LITHON-E0131: field '{attr}' is bound twice in this arm (4.7)")
                if not (isinstance(sub, ast.MatchAs) and sub.name and sub.pattern is None):
                    raise NotImplementedError(
                        f"LITHON-E0131: field '{attr}' must be bound to a plain name (4.7)")
                binding[attr] = sub.name
            missing = [fn for fn in fields if fn not in binding]
            if missing:
                raise NotImplementedError(
                    f"LITHON-E0131: arm '{enum_name}.{cls.attr}' must bind every payload field; "
                    f"missing {', '.join(missing)} (4.7)")
            return cls.attr, binding
        raise NotImplementedError(
            f"LITHON-E0131: unsupported match pattern {type(pattern).__name__} -- 4.7 arms are "
            f"{enum_name}.V, {enum_name}.V(bindings) or `case _:` (4.7)")

    def _bind_name(self, name, reg, kind, width):
        """Declares a match binding `name` of the given scalar type and stores the
        loaded payload value into it."""
        if name.startswith("_"):
            raise NotImplementedError(
                f"LITHON-E0131: a match binding cannot start with '_'; that prefix is reserved "
                f"for pointers (4.4)")
        self.emit(f"store {name}, {reg}{render_type_suffix(kind, width)}")
        self.declared[name] = (kind, width, None, None, None, None)
        if kind in ("int", "float", "bool"):
            self.var_types[name] = (kind, width)

    def _bind_enum_payload(self, var, base_off, layout, variant, binding):
        """Emits the fieldload(s) that bind a variant's payload inside its arm."""
        payload = variant["payload"]
        if payload is None:
            return
        pay_off = base_off + layout.payload_offset
        if payload["kind"] in ("int", "float", "bool"):
            reg = self.new_reg()
            self.emit(f"{reg} = fieldload {var}, {pay_off}"
                      f"{render_type_suffix(payload['kind'], payload['width'])}")
            self._bind_name(binding, reg, payload["kind"], payload["width"])
            return
        fields = self.structs[payload["struct_name"]].fields
        for field_name, bind_name in binding.items():
            field = fields[field_name]
            off = pay_off + field["offset"]
            reg = self.new_reg()
            self.emit(f"{reg} = fieldload {var}, {off}"
                      f"{render_type_suffix(field['kind'], field['width'])}")
            self._bind_name(bind_name, reg, field["kind"], field["width"])

    def build_match(self, node):
        place = self._resolve_enum_place(node.subject)
        if place is None:
            raise NotImplementedError(
                "LITHON-E0131: `match` reads an enum; its subject must be an enum variable or "
                "an enum struct field (4.7)")
        var, base_off, enum_name = place
        layout = self.enums[enum_name]

        arms = []
        wildcard_index = None
        for i, case in enumerate(node.cases):
            if case.guard is not None:
                raise NotImplementedError(
                    "LITHON-E0131: `case ... if <guard>` is not supported yet (4.7)")
            pattern = case.pattern
            if isinstance(pattern, ast.MatchAs) and pattern.name is None \
                    and pattern.pattern is None:
                arms.append(("wildcard", None, case.body))
                wildcard_index = i
            else:
                variant, binding = self._parse_enum_pattern(pattern, enum_name)
                arms.append((variant, binding, case.body))

        if wildcard_index is None:
            covered = {a[0] for a in arms}
            missing = [v["name"] for v in layout.variants if v["name"] not in covered]
            if missing:
                raise NotImplementedError(
                    f"LITHON-E0131: `match` on enum '{enum_name}' is not exhaustive -- no arm "
                    f"covers {', '.join(missing)}; add it or a `case _:` (4.7)")
        elif wildcard_index != len(arms) - 1:
            raise NotImplementedError(
                "LITHON-E0131: `case _:` must be the last arm -- arms after it are "
                "unreachable (4.7)")

        tag_reg = self.new_reg()
        self.emit(f"{tag_reg} = fieldload {var}, {base_off}"
                  f"{render_type_suffix('int', layout.tag_width)}")

        merge_label = self.reserve_label()
        for i, (variant, binding, body) in enumerate(arms):
            last = (i == len(arms) - 1)
            if variant == "wildcard":
                self._bind_and_build(var, base_off, layout, None, None, body)
                self.emit(f"jump {merge_label}")
                break
            v = layout.variant_by_name[variant]
            const_reg = self.new_reg()
            self.emit(f"{const_reg} = const_i64 {v['tag']}")
            cond_reg = self.new_reg()
            self.emit(f"{cond_reg} = eq {tag_reg}, {const_reg}")
            then_label = self.reserve_label()
            next_label = merge_label if last else self.reserve_label()
            self.emit(f"branch {cond_reg}, {then_label}, {next_label}")
            self.start_block(then_label)
            self._bind_and_build(var, base_off, layout, v, binding, body)
            self.emit(f"jump {merge_label}")
            if not last:
                self.start_block(next_label)
        self.start_block(merge_label)

    def _bind_and_build(self, var, base_off, layout, variant, binding, body):
        if variant is not None:
            self._bind_enum_payload(var, base_off, layout, variant, binding)
        for stmt in body:
            self.build_stmt(stmt)

    def build_if(self, node):
        cond_reg = self.build_expr(node.test)
        branch_block = self.current

        then_label = self.reserve_label()
        else_label = self.reserve_label() if node.orelse else None
        merge_label = self.reserve_label()

        target_else = else_label if else_label else merge_label
        branch_block.lines.append(f"    branch {cond_reg}, {then_label}, {target_else}")
        self.note_terminator_targets(f"branch {cond_reg}, {then_label}, {target_else}")
        self.terminated = True

        self.start_block(then_label)
        for stmt in node.body:
            self.build_stmt(stmt)
        self.emit(f"jump {merge_label}")

        if node.orelse:
            self.start_block(else_label)
            for stmt in node.orelse:
                self.build_stmt(stmt)
            self.emit(f"jump {merge_label}")

        self.start_block(merge_label)

    def build_while(self, node):
        if node.orelse:
            raise NotImplementedError("LITHON-E0118: while/else is not supported (the else clause would be silently dropped)")
        header_label = self.reserve_label()
        body_label = self.reserve_label()
        exit_label = self.reserve_label()

        self.emit(f"jump {header_label}")

        self.start_block(header_label)
        cond_reg = self.build_expr(node.test)
        self.emit(f"branch {cond_reg}, {body_label}, {exit_label}")

        self.start_block(body_label)
        self.loop_stack.append((header_label, exit_label))
        for stmt in node.body:
            self.build_stmt(stmt)
        self.loop_stack.pop()
        self.emit(f"jump {header_label}")

        self.start_block(exit_label)

    def _container_capacity(self, name):
        """The declared capacity N of a list/tuple variable, or None.

        Phase 6.1: enumerate/zip need a compile-time bound, and this is where it
        comes from. `xs: list[int[64], 10]` records width=10 against the name,
        so the bound is a constant here rather than a runtime length -- which is
        exactly what makes these helpers cheap: the loop is a counted loop with
        a literal limit, and the elements are indexed with a bounds check the
        typechecker already understands.
        """
        decl = self.declared.get(name)
        if decl is None:
            return None
        kind, width = decl[0], decl[1]
        if kind not in ("list", "tuple"):
            return None
        return width

    def _plan_iteration_helper(self, fname, node):
        """Resolves `for a, b in enumerate(xs)` / `for a, b in zip(xs, ys)`.

        Returns (counter_name, args, binds) where `args` is the one-element
        argument list for the ordinary counted-loop path below and `binds` is a
        list of (target_name, container_name) bound at the top of each
        iteration.

        Both helpers are a frontend REWRITE over constructs that already exist,
        not new IR: the pair (index, element) never has to exist as a value.
        That is the whole reason this is cheap. `enumerate(xs)` becomes
        `for i in range(N): x = xs[i]`, and `zip(xs, ys)` becomes the same loop
        bounded at min(N1, N2) -- a compile-time constant, because len() folds
        to the declared capacity. So z = zip(a, b) is refused outright: there is
        no value to hand back, and a homogeneous tuple cannot represent
        enumerate's (int, T) pair anyway.
        """
        call = node.iter
        args = call.args

        def source_name(arg, what):
            if not isinstance(arg, ast.Name):
                raise NotImplementedError(
                    f"LITHON-E0119: {fname}(...) takes plain {what} variables in a for "
                    f"header, e.g. {fname}(xs) -- a container has no value to iterate "
                    f"yet, so it cannot be written as an expression")
            cap = self._container_capacity(arg.id)
            if cap is None:
                raise NotImplementedError(
                    f"LITHON-E0119: {fname}({arg.id}) needs a list or tuple declared with "
                    f"its capacity, e.g. `{arg.id}: list[int[64], 8] = ...`")
            return arg.id, cap

        def targets_of(n):
            t = node.target
            if not isinstance(t, ast.Tuple) or len(t.elts) != n:
                shown = ", ".join(["a"] * n) if fname == "zip" else "i, x"
                raise NotImplementedError(
                    f"LITHON-E0119: {fname}(...) in a for header binds exactly {n} "
                    f"target name{'s' if n != 1 else ''}, e.g. for {shown} in "
                    f"{fname}(...)")
            names = []
            for elt in t.elts:
                if not isinstance(elt, ast.Name):
                    raise NotImplementedError(
                        f"LITHON-E0119: a for target must be a plain name in {fname}(...)")
                names.append(elt.id)
            return names

        if fname == "enumerate":
            if len(args) != 1:
                raise NotImplementedError(
                    "LITHON-E0119: enumerate() takes exactly one container in a for header")
            src, cap = source_name(args[0], "container")
            names = targets_of(2)
            return names[0], [ast.Constant(value=cap)], [(names[1], src)]

        # zip
        if len(args) < 2:
            raise NotImplementedError(
                "LITHON-E0119: zip() takes at least two containers in a for header")
        sources = [source_name(a, "container") for a in args]
        names = targets_of(len(sources))
        # The bound is min(N1, N2, ...): zip stops at the shortest input, which
        # is Python's semantics and here is a constant rather than a comparison
        # performed at run time.
        bound = min(cap for _, cap in sources)
        # zip's targets are all ELEMENTS -- unlike enumerate there is no index
        # target to name -- so the counter is an internal name, generated so it
        # cannot collide with anything the programmer wrote.
        counter = self._fresh_loop_counter()
        binds = [(name, src) for name, (src, _) in zip(names, sources)]
        return counter, [ast.Constant(value=bound)], binds

    def _bind_target(self, name, kind, width, what):
        """Records a for/enumerate/zip target as a binding of a known type.

        6.1. A loop target is a binding like any other, so it has to obey the
        same "a name has one type" rule (V1_SPEC 0.6.10 / E0122) that an
        annotated declaration obeys. It used not to: `enumerate`'s and `zip`'s
        targets were stored but never recorded, so binding `v` to an int in one
        header and to a bool in another silently changed its type. Nothing
        errored -- the value simply became unresolved, and the print guard then
        refused the native tier with a message about the wrong thing entirely.

        Registering it closes that: the second binding is now an E0122 refusal
        naming both types, at the source line that caused it.
        """
        decl_key = (kind, width, None, None, None, None)
        prev_decl = self.declared.get(name)
        if prev_decl is not None and prev_decl != decl_key:
            def spell(d):
                return render_type_suffix(*d).lstrip(" :") or "?"
            raise NotImplementedError(
                f"LITHON-E0122: `{name}` is bound with a different type: first "
                f"bound {spell(prev_decl)}, now {spell(decl_key)} as {'an' if what[0] in 'aeiou' else 'a'} "
                f"{what} target -- a name has one type (V1_SPEC 0.6.10)")
        self.declared[name] = decl_key
        if kind in ("int", "float", "bool"):
            self.var_types[name] = (kind, width)

    def _build_reduction(self, fname, node):
        """sum / min / max / any / all, unrolled over the compile-time N.

        6.1. None of these needs a loop at run time: a list's capacity is
        known when the program is compiled (it is what `len()` folds to), so
        the walk is emitted as N straight-line steps. That is the whole reason
        the spec calls them cheap -- there is no runtime helper, no iterator
        value, and no unbounded memory anywhere in the construct.

        Consequence worth stating: a list always holds exactly N elements
        today (there is no length field until Phase 6.7), so `sum` over an
        untouched slot adds a zero and `min`/`max` see a zero too. That is
        consistent with every other operation on a fixed-capacity list, and it
        is why these read as "the N slots" rather than "the elements".
        """
        name, decl = self._reduce_list_arg(fname, node)
        kind, cap, elem_kind, elem_width = decl[0], decl[1], decl[2], decl[3]

        if fname == "sum":
            if elem_kind not in ("int", "float"):
                raise NotImplementedError(
                    f"LITHON-E0115: sum() is int/float only, {name} is "
                    f"{render_type_suffix(kind, cap, elem_kind, elem_width).lstrip(' :')}")
            acc = self.new_reg()
            self.emit(f"{acc} = const_i64 0" if elem_kind == "int"
                      else f"{acc} = const_f64 0.0")
            for k in range(cap):
                idx = self.new_reg()
                self.emit(f"{idx} = const_i64 {k}")
                el = self.new_reg()
                self.emit(f"{el} = Index {name}, {idx}")
                nxt = self.new_reg()
                self.emit(f"{nxt} = add {acc}, {el}")
                acc = nxt
            return acc

        if fname in ("min", "max"):
            if elem_kind != "int":
                raise NotImplementedError(
                    f"LITHON-E0115: {fname}() is int only in this slice, {name} is "
                    f"{render_type_suffix(kind, cap, elem_kind, elem_width).lstrip(' :')}")
            if cap == 0:
                raise NotImplementedError(
                    f"LITHON-E0115: {fname}({name}) needs a non-empty list")
            idx0 = self.new_reg()
            self.emit(f"{idx0} = const_i64 0")
            acc = self.new_reg()
            self.emit(f"{acc} = Index {name}, {idx0}")
            for k in range(1, cap):
                idx = self.new_reg()
                self.emit(f"{idx} = const_i64 {k}")
                el = self.new_reg()
                self.emit(f"{el} = Index {name}, {idx}")
                cond = self.new_reg()
                self.emit(f"{cond} = {'lt' if fname == 'min' else 'gt'} {el}, {acc}")
                acc = self._emit_select(cond, el, acc, elem_kind, elem_width)
            return acc

        # any / all
        if elem_kind != "bool":
            raise NotImplementedError(
                f"LITHON-E0115: {fname}() is bool only in this slice, {name} is "
                f"{render_type_suffix(kind, cap, elem_kind, elem_width).lstrip(' :')}")
        true_r = self.new_reg()
        false_r = self.new_reg()
        self.emit(f"{true_r} = const_bool 1")
        self.emit(f"{false_r} = const_bool 0")
        acc = true_r if fname == "any" else false_r
        for k in range(cap):
            idx = self.new_reg()
            self.emit(f"{idx} = const_i64 {k}")
            el = self.new_reg()
            self.emit(f"{el} = Index {name}, {idx}")
            # any: stay true once seen -> or. all: stay true while still true.
            nxt = self.new_reg()
            self.emit(f"{nxt} = or {acc}, {el}" if fname == "any"
                      else f"{nxt} = and {acc}, {el}")
            acc = nxt
        return acc

    def build_for(self, node):
        if node.orelse:
            raise NotImplementedError("LITHON-E0118: for/else is not supported (the else clause would be silently dropped)")

        # Phase 6.1: enumerate/zip are for-header rewrites. They resolve to the
        # ordinary counted loop with a literal limit, plus the element bindings
        # replayed at the top of each iteration.
        binds = []
        iter_call = node.iter
        iter_name = (iter_call.func.id
                     if isinstance(iter_call, ast.Call) and isinstance(iter_call.func, ast.Name)
                     else None)
        if iter_name in ("enumerate", "zip"):
            loop_var, args, binds = self._plan_iteration_helper(iter_name, node)
        else:
            if not isinstance(node.target, ast.Name):
                raise NotImplementedError("LITHON-E0119: only a single name for-target is supported")
            if not (isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "range"):
                raise NotImplementedError("LITHON-E0119: only for x in range(...) is supported")
            args = node.iter.args
            loop_var = node.target.id
            if len(args) not in (1, 2, 3):
                raise NotImplementedError("LITHON-E0119: range takes 1, 2, or 3 arguments")
        # For now, all int[64] as per decision (user said i:int[64])
        start_reg = None
        limit_reg = None
        step_reg = None

        if len(args) == 1:
            start_reg = self.new_reg()
            self.emit(f"{start_reg} = const_i64 0")
            limit_reg = self.build_expr(args[0])
            step_reg = self.new_reg()
            self.emit(f"{step_reg} = const_i64 1")
        elif len(args) == 2:
            start_reg = self.build_expr(args[0])
            limit_reg = self.build_expr(args[1])
            step_reg = self.new_reg()
            self.emit(f"{step_reg} = const_i64 1")
        elif len(args) == 3:
            start_reg = self.build_expr(args[0])
            limit_reg = self.build_expr(args[1])
            step_reg = self.build_expr(args[2])
            # check if literal step is 0
            # try to detect const_i64 0 in value? easier: check AST literal
            pass

        # handle step==0 literal
        if len(args) == 3 and isinstance(args[2], ast.Constant) and isinstance(args[2].value, (int, float)):
            sv = int(args[2].value)
            if sv == 0:
                raise NotImplementedError("LITHON-E0107: range(a,b,0): step is zero")
            # normalize to int[64] if small

        # A for-target is stored before it is read but never declared, so it is
        # in none of the name collections -- record it so a later generated
        # counter (6.1 zip) cannot collide with it.
        self.loop_counters.add(loop_var)
        self.emit("store " + loop_var + ", " + start_reg + " : int[64]")

        body_label = self.reserve_label()
        exit_label = self.reserve_label()
        latch_label = self.reserve_label()

        if len(args) in (1, 2):
            header_label = self.reserve_label()
            self.emit(f"jump {header_label}")

            self.start_block(header_label)
            i_reg = self.new_reg()
            self.emit(f"{i_reg} = load {loop_var}")
            cond_reg = self.new_reg()
            self.emit(f"{cond_reg} = lt {i_reg}, {limit_reg}")
            self.emit(f"branch {cond_reg}, {body_label}, {exit_label}")

            self.start_block(body_label)
            # Phase 6.1: bind the element(s) before the body runs, which is what
            # makes `for i, x in enumerate(xs)` and `for a, b in zip(xs, ys)`
            # work without the pair ever being a value. The index is the loop
            # counter itself, so it is already bound by the loop's own store.
            if binds:
                cur = self.new_reg()
                self.emit(f"{cur} = load {loop_var}")
                for elem_name, container in binds:
                    kind, width, elem_kind, elem_width = \
                        self.declared[container][:4]
                    self._bind_target(elem_name, elem_kind, elem_width,
                                     "enumerate" if len(binds) == 1 else "zip")
                    er = self.new_reg()
                    self.emit(f"{er} = Index {container}, {cur}")
                    self.emit(f"store {elem_name}, {er}"
                              f"{render_type_suffix(elem_kind, elem_width)}")
            self.loop_stack.append((latch_label, exit_label))
            for stmt in node.body:
                self.build_stmt(stmt)
            self.loop_stack.pop()
            self.emit(f"jump {latch_label}")

            self.start_block(latch_label)
            i_reg2 = self.new_reg()
            self.emit(f"{i_reg2} = load {loop_var}")
            one_reg = self.new_reg()
            self.emit(f"{one_reg} = const_i64 1")
            inc_reg = self.new_reg()
            self.emit(f"{inc_reg} = add {i_reg2}, {one_reg}")
            self.emit(f"store {loop_var}, {inc_reg}")
            self.emit(f"jump {header_label}")

            self.start_block(exit_label)
            return

        # 3 args: rotated form. The element test guards the first iteration;
        # thereafter the latch exits as soon as the unsigned remaining distance
        # is <= the step magnitude, so the increment is only performed when it
        # provably stays strictly inside the range (no signed overflow / E0303).
        if not (isinstance(args[2], ast.Constant)):
            self.emit(f"guardnonzero {step_reg}")

        zstep = self.new_reg()
        self.emit(f"{zstep} = const_i64 0")
        step_gt0 = self.new_reg()
        self.emit(f"{step_gt0} = gt {step_reg}, {zstep}")

        pre_pos = self.reserve_label()
        pre_neg = self.reserve_label()
        self.emit(f"branch {step_gt0}, {pre_pos}, {pre_neg}")

        self.start_block(pre_pos)
        pc = self.new_reg()
        self.emit(f"{pc} = lt {start_reg}, {limit_reg}")
        self.emit(f"branch {pc}, {body_label}, {exit_label}")

        self.start_block(pre_neg)
        nc = self.new_reg()
        self.emit(f"{nc} = gt {start_reg}, {limit_reg}")
        self.emit(f"branch {nc}, {body_label}, {exit_label}")

        self.start_block(body_label)
        self.loop_stack.append((latch_label, exit_label))
        for stmt in node.body:
            self.build_stmt(stmt)
        self.loop_stack.pop()
        self.emit(f"jump {latch_label}")

        self.start_block(latch_label)
        li = self.new_reg()
        self.emit(f"{li} = load {loop_var}")
        lat_pos = self.reserve_label()
        lat_neg = self.reserve_label()
        self.emit(f"branch {step_gt0}, {lat_pos}, {lat_neg}")

        self.start_block(lat_pos)
        pM = self.new_reg()
        self.emit(f"{pM} = const_i64 -9223372036854775808")
        pd = self.new_reg()
        self.emit(f"{pd} = wrapsub {limit_reg}, {li}")
        pd2 = self.new_reg()
        ps2 = self.new_reg()
        self.emit(f"{pd2} = bxor {pd}, {pM}")
        self.emit(f"{ps2} = bxor {step_reg}, {pM}")
        pcont = self.new_reg()
        self.emit(f"{pcont} = gt {pd2}, {ps2}")
        pinc = self.reserve_label()
        self.emit(f"branch {pcont}, {pinc}, {exit_label}")

        self.start_block(lat_neg)
        nM = self.new_reg()
        self.emit(f"{nM} = const_i64 -9223372036854775808")
        nd = self.new_reg()
        self.emit(f"{nd} = wrapsub {li}, {limit_reg}")
        nstep = self.new_reg()
        self.emit(f"{nstep} = sub {zstep}, {step_reg}")
        nd2 = self.new_reg()
        ns2 = self.new_reg()
        self.emit(f"{nd2} = bxor {nd}, {nM}")
        self.emit(f"{ns2} = bxor {nstep}, {nM}")
        ncont = self.new_reg()
        self.emit(f"{ncont} = gt {nd2}, {ns2}")
        self.emit(f"branch {ncont}, {pinc}, {exit_label}")

        self.start_block(pinc)
        vi = self.new_reg()
        self.emit(f"{vi} = load {loop_var}")
        ni = self.new_reg()
        self.emit(f"{ni} = add {vi}, {step_reg}")
        self.emit(f"store {loop_var}, {ni}")
        self.emit(f"jump {body_label}")

        self.start_block(exit_label)

    def build_tuple_declaration(self, name, capacity, suffix, value_node):
        """Emits a tuple declaration, with or without a tuple literal.

        4.2. The valueless store comes first and is not optional. It reserves
        and zeroes the run, and both execution tiers size N slots from it.

        The literal's elements then arrive as N IndexStore instructions, which
        is the same opcode an illegal t[0] = v would use. There is
        deliberately no second opcode for building a tuple. Adding one would
        give the immutability rule two places to live, and one of them would be
        a runtime check. Instead the type checker recognises exactly this
        sequence, a valueless declaration followed by its own N element stores,
        and refuses every other IndexStore into a tuple.

        So the literal is the only way to get a non zero value into a tuple, and
        it is checked rather than trusted. Arity, element kinds and widths all
        go through the normal element rules.
        """
        self.emit(f"store {name}{suffix}")
        if value_node is None:
            return
        if not isinstance(value_node, ast.Tuple):
            raise NotImplementedError(
                f"LITHON-E0123: a tuple is built from a tuple literal, so `{name}: ... = <expr>` "
                f"needs (a, b, ...), not a single expression (4.2)")
        if len(value_node.elts) != capacity:
            raise NotImplementedError(
                f"LITHON-E0123: `{name}: tuple[..., {capacity}]` needs exactly {capacity} element"
                f"{'' if capacity == 1 else 's'}, got {len(value_node.elts)}")
        for i, elt in enumerate(value_node.elts):
            # A nested container literal would need the outer stride to be the
            # inner's byte size, which is the same non-power-of-two SIB limit
            # that rejects nested containers as a type.
            if isinstance(elt, (ast.Tuple, ast.List)):
                raise NotImplementedError(
                    f"LITHON-E0123: tuple element {i} is a container literal; 4.2 tuples hold "
                    f"int, float or bool elements only")
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                raise NotImplementedError(
                    f"LITHON-E0123: tuple element {i} is a string literal; 4.2 tuples hold "
                    f"int, float or bool elements only")
            idx_reg = self.new_reg()
            self.emit(f"{idx_reg} = const_i64 {i}")
            val_reg = self.build_expr(elt)
            self.emit(f"IndexStore {name}, {idx_reg}, {val_reg}")

    def build_dict_declaration(self, name, capacity, suffix, value_node):
        """Emits a dict declaration, with or without a dict literal.

        4.3. The valueless store comes first, for the same reason a tuple has
        one. It is what reserves the table, and it is what zeroes the occupied
        array, so a bare declaration is an empty dict rather than a dict full of
        zero valued entries.

        Each entry then arrives as a DictStore carrying the key and the value.
        There is deliberately no bucket operand. The bucket is the hash of the
        key, and the key has to be a constant, so both execution tiers can
        resolve it while they compile this instruction instead of walking a probe
        sequence on every entry. Emitting the bucket here instead would mean two
        independent hash implementations, one per tier, that could disagree and
        only fail once a lookup missed.

        The literal may hold fewer entries than there are buckets, because N is
        the bucket count and not the entry count. That is the normal case for a
        hash table and it is what makes probing reachable at all.
        """
        self.emit(f"store {name}{suffix}")
        if value_node is None:
            return
        if not isinstance(value_node, ast.Dict):
            raise NotImplementedError(
                f"LITHON-E0123: a dict is built from a dict literal, so `{name}: ... = <expr>` "
                f"needs {{k: v, ...}}, not a single expression (4.3)")
        if len(value_node.keys) > capacity:
            raise NotImplementedError(
                f"LITHON-E0123: `{name}: dict[..., {capacity}]` has {capacity} bucket"
                f"{'' if capacity == 1 else 's'}, so at most {capacity} entrie"
                f"{'' if capacity == 1 else 's'} fit, got {len(value_node.keys)}")
        for i, (key_node, val_node) in enumerate(zip(value_node.keys, value_node.values)):
            # A key must be a constant, because the bucket is its hash. A
            # computed key would turn construction into a run time insert and
            # bring back the probe loop this design is built to avoid.
            #
            # A negative literal arrives as UnaryOp(USub, Constant), not as a
            # Constant, so `-1: v` would otherwise be read as a computed key and
            # refused. It is still a literal, and a key table made only of
            # negative ints is not an edge case worth locking out.
            if isinstance(key_node, ast.UnaryOp) and \
                    isinstance(key_node.op, ast.USub) and \
                    isinstance(key_node.operand, ast.Constant) and \
                    not isinstance(key_node.operand.value, (str, float, bool)):
                key_node = ast.Constant(value=-key_node.operand.value)
            if not isinstance(key_node, ast.Constant):
                raise NotImplementedError(
                    f"LITHON-E0123: dict key {i} is not a literal; a dict key must be a "
                    f"constant, because the bucket is decided from it (4.3)")
            if isinstance(key_node.value, str):
                raise NotImplementedError(
                    f"LITHON-E0123: dict key {i} is a string; 4.3 keys are int or bool only")
            if isinstance(key_node.value, float):
                raise NotImplementedError(
                    f"LITHON-E0123: dict key {i} is a float; 4.3 keys are int or bool only")
            key_reg = self.new_reg()
            # A bool key is emitted as a bool constant, not as int(key). The type
            # of the key register is what the checker compares against the
            # declared key type, so `dict[bool, ...] = {True: 1}` lowered to
            # const_i64 would reject its own literal.
            if isinstance(key_node.value, bool):
                self.emit(f"{key_reg} = const_bool {1 if key_node.value else 0}")
            else:
                self.emit(f"{key_reg} = const_i64 {int(key_node.value)}")
            val_reg = self.build_expr(val_node)
            self.emit(f"DictStore {name}, {key_reg}, {val_reg}")

    def declared_suffix(self, name):
        """The " : T" a plain `name = ...` store must carry for a name this
        function declared as a SCALAR or a pointer, or "" for anything else.

        Only those two kinds are baked in: their declarations emit no store of
        their own, so the first assignment is the only place the checker can
        see the type (0.6.5 range check, 0.6.10 definite assignment).
        Containers declare themselves with a valueless store that already
        carries the suffix, and re-assigning one goes through the checker's
        unannotated path as before, so they are left alone."""
        decl = self.declared.get(name)
        if decl is None:
            return ""
        if decl[0] not in ("int", "float", "bool", "ptr"):
            return ""
        return render_type_suffix(*decl)

    def build_stmt(self, node):
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                raise NotImplementedError("LITHON-E0121: only simple name targets are supported for annotations")
            name = node.target.id
            # 4.6. A struct declaration names the type on the left of the colon:
            # `s: SockAddrIn`. It is recognised before the scalar path because a
            # struct name is not a type string parse_type_annotation knows -- it
            # is a user-declared type, and only this builder's registry says so.
            if isinstance(node.annotation, ast.Name) and node.annotation.id in self.structs:
                layout = self.structs[node.annotation.id]
                decl_key = ("struct", layout.size, node.annotation.id, None, None, None)
                prev_decl = self.declared.get(name)
                if prev_decl is not None and prev_decl != decl_key:
                    raise NotImplementedError(
                        f"LITHON-E0122: `{name}` is re-declared with a different type -- a name "
                        f"has one type (V1_SPEC 0.6.10)")
                self.declared[name] = decl_key
                if node.value is not None:
                    raise NotImplementedError(
                        f"LITHON-E0128: `{name}: {node.annotation.id}` cannot take a value -- a "
                        f"struct has no value; declare it and assign its fields one at a "
                        f"time (4.6)")
                self.struct_vars[name] = node.annotation.id
                self.emit(f"store {name} : struct[{layout.size}]")
                return
            # 4.7. An enum declaration looks like a struct one, but it may carry
            # a construction as its initializer: `e: E = E.A2(5)`. The blob is
            # reserved first (the same valueless store a struct uses), then the
            # constructor writes the tag and payload into it.
            if isinstance(node.annotation, ast.Name) and node.annotation.id in self.enums:
                layout = self.enums[node.annotation.id]
                decl_key = ("enum", layout.size, node.annotation.id, None, None, None)
                prev_decl = self.declared.get(name)
                if prev_decl is not None and prev_decl != decl_key:
                    raise NotImplementedError(
                        f"LITHON-E0122: `{name}` is re-declared with a different type -- a name "
                        f"has one type (V1_SPEC 0.6.10)")
                self.declared[name] = decl_key
                self.enum_vars[name] = node.annotation.id
                self.emit(f"store {name} : struct[{layout.size}]")
                if node.value is not None:
                    self._build_enum_construction(name, 0, node.annotation.id, node.value)
                return
            kind, width, elem_kind, elem_width, key_kind, key_width = \
                parse_type_annotation(node.annotation, self.structs)
            suffix = render_type_suffix(kind, width, elem_kind, elem_width,
                                        key_kind, key_width)
            # 0.6.10. A name declares its type once per function. Writing a
            # DIFFERENT type on a second declaration of the same name has no
            # single answer for what the name holds, so it is refused here,
            # where both declarations are visible in source order. Same type
            # re-declaration is fine (it is how a name is re-opened, e.g. a
            # tuple restart).
            decl_key = (kind, width, elem_kind, elem_width, key_kind, key_width)
            prev_decl = self.declared.get(name)
            if prev_decl is not None and prev_decl != decl_key:
                def spell(d):
                    return render_type_suffix(*d).lstrip(" :") or "?"
                raise NotImplementedError(
                    f"LITHON-E0122: `{name}` is re-declared with a different type: first "
                    f"declared {spell(prev_decl)}, now {spell(decl_key)} -- "
                    f"a name has one type (V1_SPEC 0.6.10)")
            self.declared[name] = decl_key
            if kind == "tuple":
                self.build_tuple_declaration(name, width, suffix, node.value)
                return
            if kind == "dict":
                self.dict_names.add(name)
                self.build_dict_declaration(name, width, suffix, node.value)
                return
            if kind == "ptr":
                # 4.4. The underscore half of the naming rule, enforced here so
                # a bad name is refused at source instead of by the checker's
                # backstop on hand-written IR.
                if not name.startswith("_"):
                    raise NotImplementedError(
                        f"LITHON-E0122: a pointer is named with a leading underscore, write "
                        f"`_{name}: ptr[...]` or pick a name that starts with "
                        f"'_' (4.4)")
                self.ptr_names.add(name)
                self.var_types[name] = (elem_kind, elem_width)
                if node.value is None:
                    # 0.6.10. A bare `_p: ptr[T]` is a legal declaration: it
                    # names the type and emits nothing else, so _p starts
                    # UNASSIGNED -- not null. There is still no null pointer
                    # in Lithon: the first `_p = addressof(...)` is what
                    # assigns it, and a read before that is the same
                    # "not definitely assigned" error as for any scalar.
                    return
                value_reg = self.build_expr(node.value)
                self.emit(f"store {name}, {value_reg}{suffix}")
                return
            if kind in ("int", "float", "bool"):
                # 4.4. The underscore rule's other half: an annotated scalar may
                # not take a pointer's reserved first character.
                if name.startswith("_"):
                    raise NotImplementedError(
                        f"LITHON-E0122: a name that starts with '_' must be a pointer, but "
                        f"`{name}` is declared {suffix.lstrip() or kind} (4.4)")
                self.var_types[name] = (kind, width)
            if node.value is not None:
                value_reg = self.build_expr(node.value)
                self.emit(f"store {name}, {value_reg}{suffix}")
                return
            # An annotation-only declaration emits nothing for a SCALAR: there
            # is no value to store and no storage to reserve. The declaration
            # itself lives in self.declared, and the FIRST plain `name = ...`
            # below carries its type into the IR as the store's annotation --
            # which is what makes `i: int[8]; i = 300` a 0.6.5 range error and
            # a read before that assignment a 0.6.10 error, while the machine
            # code of `i: int[8]; i = 5` stays byte-identical to
            # `i: int[8] = 5`.
            #
            # A container is different and this is the subtle part. `xs:
            # list[int[64], 4]` with no value is not an empty declaration to be
            # ignored -- it is what reserves and ZEROES the run. Both execution
            # tiers key off this instruction: the allocator sizes N slots from it,
            # and the interpreter materialises N zeroed elements from it. Emit
            # nothing here and `xs` has no storage at all, so the first
            # `xs[0] = 1` writes to a name that was never allocated. This is the
            # reason the declaration is a `store` with no value operand rather
            # than a separate opcode.
            if kind in CONTAINER_KINDS:
                self.emit(f"store {name}{suffix}")
            return

        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                raise NotImplementedError("LITHON-E0121: only single-target assignment is supported")
            target = node.targets[0]
            if isinstance(target, ast.Subscript):
                # `xs[i] = v`. Op::IndexStore carries the container name in `name`
                # and only the index and value as ValueIds.
                if not isinstance(target.value, ast.Name):
                    raise NotImplementedError(
                        "LITHON-E0117: only a plain variable can be index-assigned, e.g. xs[i] = v")
                # 4.3. A dict subscript read is DictIndex, but there is no dict
                # subscript WRITE, so `d[k] = v` would quietly become an
                # IndexStore into a dict and be reported as a missing index
                # operation rather than as the immutability violation it is.
                if target.value.id in self.dict_names:
                    raise NotImplementedError(
                        f"LITHON-E0117: cannot store into '{target.value.id}': a dict is "
                        f"immutable (4.3); it is filled by its own literal, and "
                        f"after that the only operations are a read and contains")
                idx = self.build_expr(target.slice)
                val = self.build_expr(node.value)
                self.emit(f"IndexStore {target.value.id}, {idx}, {val}")
                return
            # 4.7. `e = E.V(...)` (an enum variable) or `s.e = E.V(...)` (an
            # enum-typed struct field) is a construction written into the blob:
            # the tag and the active payload's fields at fixed offsets. Nothing
            # else can be assigned to an enum place, so a non-constructor value
            # is refused here with a name for the reason.
            enum_place = self._resolve_enum_place(target)
            if enum_place is not None:
                self._build_enum_construction(enum_place[0], enum_place[1],
                                              enum_place[2], node.value)
                return
            # 4.6. `s.field = v`, and nested `s.inner.field = v`, resolve to one
            # fieldstore at the summed byte offset. The value is a scalar (a
            # struct-typed leaf is refused by the resolver), so no whole-struct
            # assignment can arise here.
            if isinstance(target, ast.Attribute):
                var, offset, kind, width = self._resolve_struct_field(target)
                val_reg = self.build_expr(node.value)
                self.emit(f"fieldstore {var}, {offset}, {val_reg}{render_type_suffix(kind, width)}")
                return
            if not isinstance(target, ast.Name):
                raise NotImplementedError("LITHON-E0121: only single-name assignment targets are supported")
            # 4.6. A whole-struct assignment has no meaning: there is no struct
            # value to copy. The fields are the only writable surface.
            if target.id in self.struct_vars:
                raise NotImplementedError(
                    f"LITHON-E0128: `{target.id} = ...` cannot assign a whole struct -- assign "
                    f"its fields, e.g. {target.id}.<field> = ... (4.6)")
            if isinstance(node.value, ast.Tuple):
                # 4.2. A tuple literal belongs to its annotated declaration, where
                # the element type and the capacity are known. Naming the form
                # beats letting it fall through to the generic "expression node
                # Tuple not supported yet", which is true and useless.
                raise NotImplementedError(
                    f"LITHON-E0123: a tuple is built on its annotation, so write "
                    f"`{target.id}: tuple[int[64], {len(node.value.elts)}] = "
                    f"({', '.join(['1'] * len(node.value.elts))})` (4.2)")
            value_reg = self.build_expr(node.value)
            name = target.id
            # A declared scalar or pointer carries its type here, so the first
            # assignment after `x: T` is checked against T like an initializer
            # would be -- while an undeclared name stays unannotated and keeps
            # the "assigned without a type annotation" (0.6.1) refusal.
            self.emit(f"store {name}, {value_reg}{self.declared_suffix(name)}")
            return

        if isinstance(node, ast.AugAssign):
            # `x op= expr` lowers to exactly the IR of `x = x op expr`
            # (load x, evaluate expr, op, store x), including Python's
            # left-to-right evaluation order, so the type-checker and
            # both execution tiers see one canonical shape.
            if not isinstance(node.target, ast.Name):
                raise NotImplementedError("LITHON-E0121: only simple name targets are supported for augmented assignment")
            op_map = BITWISE_OPS if type(node.op) in BITWISE_OPS else BINARY_OPS
            op_type = type(node.op)
            if op_type not in op_map:
                raise NotImplementedError(f"LITHON-E0114: augmented operator {op_type.__name__} not supported yet")
            name = node.target.id
            if name in self.struct_vars:
                raise NotImplementedError(
                    f"LITHON-E0128: cannot use `{name} <op>=` on a struct -- assign its fields "
                    f"one at a time, e.g. {name}.<field> += ... (4.6)")
            current = self.new_reg()
            self.emit(f"{current} = load {name}")
            if name in self.ptr_names:
                # 4.4. `_p += k` is pointer arithmetic, so the RHS is a literal
                # element count scaled to bytes, same as `_p + k` in expressions
                # (see the BinOp case). The step lands on the tagged pointer;
                # the register below is the pointer's new value.
                if op_type not in (ast.Add, ast.Sub):
                    raise NotImplementedError(
                        "LITHON-E0125: a pointer only walks add and sub, got an augmented " +
                        f"operator for `{name}` (4.4)")
                rhs_lit = self.literal_int(node.value)
                if rhs_lit is None:
                    raise NotImplementedError(
                        f"LITHON-E0125: a pointer offset must be a literal integer, e.g. "
                        f"`{name} += 1` (4.4)")
                k, w = self.var_types[name]
                off = self.new_reg()
                self.emit(f"{off} = const_i64 {rhs_lit * scalar_cell_bytes(k, w)}")
                result = self.new_reg()
                self.emit(f"{result} = {op_map[op_type]} {current}, {off}")
                self.emit(f"store {name}, {result}")
                return
            rhs = self.build_expr(node.value)
            result = self.new_reg()
            self.emit(f"{result} = {op_map[op_type]} {current}, {rhs}")
            self.emit(f"store {name}, {result}")
            return

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                if len(call.args) != 1:
                    raise NotImplementedError("LITHON-E0115: print() with exactly one argument is supported")
                arg_reg = self.build_expr(call.args[0])
                self.emit(f"call print, {arg_reg}")
                return
            if isinstance(call.func, ast.Attribute):
                # 4.9. `recv.method(args)` as a statement. A method that returns
                # a value is allowed here -- the result is simply discarded.
                self._inline_method_call(call)
                return
            self.build_expr(call)
            return

        if isinstance(node, ast.If):
            self.build_if(node)
            return

        if isinstance(node, ast.While):
            self.build_while(node)
            return

        if isinstance(node, ast.For):
            self.build_for(node)
            return

        if isinstance(node, ast.Match):
            self.build_match(node)
            return

        if isinstance(node, ast.Return):
            # 4.9. Inside an inlined method a `return` is not the enclosing
            # function's return: it stores the method's result (if it has one)
            # into the inline's temp and jumps to the inline's end label.
            if self.method_return_stack:
                result_temp, ret_label = self.method_return_stack[-1]
                if node.value is not None:
                    if result_temp is None:
                        raise NotImplementedError(
                            f"LITHON-E0135: this method returns no value, so `return <expr>` is "
                            f"not allowed here (4.9)")
                    val_reg = self.build_expr(node.value)
                    self.emit(f"store {result_temp}, {val_reg}"
                              f"{self.declared_suffix(result_temp)}")
                elif result_temp is not None:
                    raise NotImplementedError(
                        f"LITHON-E0135: this method returns a value, so a bare `return` is not "
                        f"allowed here (4.9)")
                self.emit(f"jump {ret_label}")
                return
            if node.value is not None:
                val_reg = self.build_expr(node.value)
                self.emit(f"return {val_reg}")
            else:
                self.emit("return")
            return

        if isinstance(node, ast.Break):
            if not self.loop_stack:
                raise NotImplementedError("LITHON-E0120: break outside of a loop")
            _, break_target = self.loop_stack[-1]
            self.emit(f"jump {break_target}")
            return

        if isinstance(node, ast.Continue):
            if not self.loop_stack:
                raise NotImplementedError("LITHON-E0120: continue outside of a loop")
            continue_target, _ = self.loop_stack[-1]
            self.emit(f"jump {continue_target}")
            return

        raise NotImplementedError(f"LITHON-E0114: statement node {type(node).__name__} not supported yet")

    def render(self, header_line):
        out = [header_line]
        for b in self.blocks:
            out.append(f"{b.label}:")
            out.extend(b.lines)
        return "\n".join(out)


def build_program(tree):
    global TYPE_FUNCTIONS, _TYPE_FUNC_CACHE, _TYPE_FUNC_ACTIVE
    module_parts = []

    # 4.8. Type functions are resolved before any annotation is parsed, because
    # `parse_type_annotation` is where a call is expanded. The cache and the
    # active stack are reset here so the run starts from a clean slate.
    TYPE_FUNCTIONS = build_type_functions(tree)
    _TYPE_FUNC_CACHE = {}
    _TYPE_FUNC_ACTIVE = []

    # 4.6/4.7. Struct and enum definitions are collected in one source-order pass
    # before anything is built, so either kind is visible to every function and
    # to later definitions. A ClassDef that derives from neither `Struct` nor
    # `Enum` is refused here (E0126), which is what lets the statement loop below
    # skip every ClassDef without a second check.
    structs, enums = build_type_registry(tree)

    # 4.8. Type functions, structs and enums share one type namespace.
    for name in TYPE_FUNCTIONS:
        if name in structs or name in enums:
            raise NotImplementedError(
                f"LITHON-E0132: type function '{name}' collides with a struct or enum of "
                f"the same name (4.8)")

    user_fn_names = {s.name for s in tree.body
                     if isinstance(s, ast.FunctionDef) and not _is_type_function_def(s)}
    for name in TYPE_FUNCTIONS:
        if name in user_fn_names:
            raise NotImplementedError(
                f"LITHON-E0132: '{name}' is declared both as a type function and as a "
                f"runtime function (4.8)")
    _reject_storage_in_type_function_bodies(structs, enums)
    fn_rename = {}
    if ENTRY_POINT in user_fn_names:
        mangled = MANGLED_PREFIX + ENTRY_POINT
        while mangled in user_fn_names:
            mangled = MANGLED_PREFIX + mangled
        fn_rename[ENTRY_POINT] = mangled

    main_builder = IRBuilder(fn_rename, structs, enums)
    main_builder.start_block(main_builder.reserve_label())

    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            # Already validated and registered; a type definition emits no code.
            continue
        if isinstance(stmt, ast.FunctionDef):
            if _is_type_function_def(stmt):
                # 4.8. A type function emits no code: it was registered above and
                # is resolved only where its name is applied in an annotation.
                continue
            fb = IRBuilder(fn_rename, structs, enums)
            fb.start_block(fb.reserve_label())

            param_strs = []
            for arg in stmt.args.args:
                if arg.annotation is not None:
                    # 4.6/4.7. A struct or an enum is storage, not a value, so it
                    # cannot be passed or returned: the function header names a
                    # kind and a width, and neither has a kind the header can
                    # carry. Refused at source with a name for the reason rather
                    # than by the generic unknown-type path, which would read as a
                    # typo.
                    if isinstance(arg.annotation, ast.Name) and arg.annotation.id in structs:
                        raise NotImplementedError(
                            f"LITHON-E0124: parameter '{arg.arg}' cannot be a struct -- a struct "
                            f"has no value to pass; a function takes int, float, bool, str or a "
                            f"container (4.6)")
                    if isinstance(arg.annotation, ast.Name) and arg.annotation.id in enums:
                        raise NotImplementedError(
                            f"LITHON-E0124: parameter '{arg.arg}' cannot be an enum -- an enum "
                            f"has no value to pass; pass the fields a function can use (4.7)")
                    kind, width, elem_kind, elem_width, key_kind, key_width = \
                        parse_type_annotation(arg.annotation)
                    if kind == "ptr":
                        # 4.4. A pointer parameter cannot survive the function
                        # header: the header records a kind and a width but no
                        # pointee, so a passed pointer would degrade into a bare
                        # address that nothing could valueof. Refused at source
                        # rather than by the checker's header review.
                        raise NotImplementedError(
                            f"LITHON-E0124: parameter '{arg.arg}' cannot be a pointer: the "
                            f"function header records no pointee, so pointers "
                            f"are not passed by value (4.4)")
                    suffix = render_type_suffix(kind, width, elem_kind, elem_width,
                                                key_kind, key_width).replace(" : ", ":")
                    param_strs.append(f"{arg.arg}{suffix}")
                else:
                    param_strs.append(arg.arg)

            return_suffix = ""
            if stmt.returns is not None:
                if isinstance(stmt.returns, ast.Name) and stmt.returns.id in structs:
                    raise NotImplementedError(
                        f"LITHON-E0124: function '{stmt.name}' cannot return a struct -- a "
                        f"struct has no value to return (4.6)")
                if isinstance(stmt.returns, ast.Name) and stmt.returns.id in enums:
                    raise NotImplementedError(
                        f"LITHON-E0124: function '{stmt.name}' cannot return an enum -- an "
                        f"enum has no value to return (4.7)")
                kind, width, elem_kind, elem_width, key_kind, key_width = \
                    parse_type_annotation(stmt.returns)
                if kind == "ptr":
                    raise NotImplementedError(
                        f"LITHON-E0124: function '{stmt.name}' cannot return a pointer: like "
                        f"parameters, the header records no pointee (4.4)")
                # " : " is stripped because the return suffix uses " -> " and the
                # scalar path below writes the brackets itself.
                return_suffix = render_type_suffix(kind, width, elem_kind, elem_width,
                                                   key_kind, key_width).replace(" : ", " -> ")

            emitted_name = fn_rename.get(stmt.name, stmt.name)
            header = f"function {emitted_name}({', '.join(param_strs)}){return_suffix}:"

            for s in stmt.body:
                fb.build_stmt(s)
            fb.emit("return")
            module_parts.append(fb.render(header))
        else:
            main_builder.build_stmt(stmt)

    main_builder.emit("return")
    module_parts.append(main_builder.render(f"function {ENTRY_POINT}():"))

    return "\n\n".join(module_parts) + "\n"


def main():
    if len(sys.argv) != 2:
        print("usage: frontend.py <source.py>", file=sys.stderr)
        return 1

    with open(sys.argv[1], "r") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
        # build_program raises NotImplementedError for every construct this
        # frontend slice refuses. Those messages are self-identifying
        # ("LITHON-E01xx: ..."), so they print bare on stderr like a tier
        # refusal would, instead of leaking a Python traceback -- one clean
        # line, a nonzero exit, nothing else.
        ir_text = build_program(tree)
    except NotImplementedError as e:
        print(f"{e}", file=sys.stderr)
        return 1

    sys.stdout.write(ir_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
