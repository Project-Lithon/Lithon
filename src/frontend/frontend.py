#!/usr/bin/env python3
"""
Lithon frontend: Python source -> Lithon IR text.

Uses Python's own `ast` module as a shortcut -- not a permanent
architecture choice, replaced by a native Lithon parser at M10.

Two modes, both supported by the same builder:
  - Untyped input (bare "x = 10", no annotations): emits IR exactly
    as before, with no type fields. Existing tests/programs/*.lio
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


class Type:
    """A resolved Lithon type, recursive so an element/key/value can be one too.

    kind is one of int/float/str/bool/struct/ptr/list/tuple/dict.
    - a scalar carries its width (bool uses -1, struct its byte size);
    - ptr carries its pointee type in `elem`;
    - list/tuple carry a capacity and an element type in `elem`;
    - dict carries a bucket count, a key type in `key` and a value type in `elem`.

    Recursion is what lets `list[tuple[int[64], 4], 2]` exist as one value: the
    old flat six-field form could only name one level of element, so a container
    inside a container had nowhere to live and was silently flattened.
    """

    __slots__ = ("kind", "width", "cap", "elem", "key", "ref")

    def __init__(self, kind, width=-1, cap=None, elem=None, key=None, ref=None):
        self.kind = kind
        self.width = width
        self.cap = cap
        self.elem = elem
        self.key = key
        self.ref = ref   # a struct/enum NAME, for the source-level spelling only

    def is_container(self):
        return self.kind in CONTAINER_KINDS

    def is_scalar(self):
        return self.kind in ("int", "float", "str", "bool", "struct")

    def _flat(self):
        """The legacy (kind, width, elem_kind, elem_width, key_kind, key_width)
        projection. Exact for a scalar and for a one-level container; a nested
        element is collapsed to its outer kind, which is what the old flat form
        always did. Only the sites that predate nesting use it."""
        if self.kind == "dict":
            return ("dict", self.cap, self.elem.kind, self.elem.width,
                    self.key.kind, self.key.width)
        if self.kind in ("list", "tuple"):
            e = self.elem
            return (self.kind, self.cap, e.kind, e.cap if e.is_container() else e.width,
                    None, None)
        if self.kind == "ptr":
            return ("ptr", -1, self.elem.kind, self.elem.width, None, None)
        return (self.kind, self.width, self.ref, None, None, None)

    def __iter__(self):
        return iter(self._flat())

    def __getitem__(self, index):
        return self._flat()[index]

    def __eq__(self, other):
        if isinstance(other, Type):
            return ((self.kind, self.width, self.cap, self.ref) ==
                    (other.kind, other.width, other.cap, other.ref)
                    and self.elem == other.elem and self.key == other.key)
        if isinstance(other, tuple):
            return self._flat() == other
        return NotImplemented

    def __hash__(self):
        return hash((self.kind, self.width, self.cap, self.ref,
                     self.elem, self.key))

    def render(self):
        """The type's IR spelling, e.g. `list[tuple[int[64], 4], 2]`."""
        k = self.kind
        if k == "dict":
            return f"dict[{self.key.render()}, {self.elem.render()}, {self.cap}]"
        if k == "ptr":
            return f"ptr[{self.elem.render()}]"
        if k in ("list", "tuple"):
            return f"{k}[{self.elem.render()}, {self.cap}]"
        if k == "str":
            return f"str[{self.cap}]"
        if k == "bool":
            return "bool"
        return f"{k}[{self.width}]"

    def cell_bytes(self):
        """Bytes one cell of this type occupies in a frame. A container's cell is
        its whole packed run of elements; a dict's is its buckets, each holding a
        key cell and a value cell, plus the occupancy that is tracked apart."""
        if self.kind == "bool":
            return 1
        # 6.2. A str[N] is N bytes: the capacity IS the byte size, unlike a
        # scalar whose `width` is a bit width.
        if self.kind == "str":
            return self.cap
        if self.kind in ("ptr", "struct"):
            return self.width
        if self.kind in ("list", "tuple"):
            return self.cap * self.elem.cell_bytes()
        if self.kind == "dict":
            return self.cap * (self.key.cell_bytes() + self.elem.cell_bytes())
        return self.width // 8

    def __repr__(self):
        return f"Type({self.render()})"


def _flat_tuple_to_type(kind, width, elem_kind, elem_width, key_kind, key_width):
    """Rebuilds a Type from the legacy flat six fields. Exact only when the
    element and key are scalars, which is the case at every remaining flat call
    site; a nested level has no flat spelling."""
    if kind == "dict":
        return Type("dict", cap=width, elem=Type(elem_kind, width=elem_width),
                    key=Type(key_kind, width=key_width))
    if kind == "ptr":
        return Type("ptr", elem=Type(elem_kind, width=elem_width))
    if kind in CONTAINER_KINDS:
        return Type(kind, cap=width, elem=Type(elem_kind, width=elem_width))
    return Type(kind, width=width)


def render_type_suffix(t, width=None, elem_kind=None, elem_width=None,
                       key_kind=None, key_width=None):
    """Renders the trailing " : T" annotation the IR parser reads. Accepts a
    recursive Type, or the legacy flat six fields (scalar/one-level only). An
    empty type (None) renders as the empty string."""
    if isinstance(t, Type):
        return f" : {t.render()}"
    if t is None:
        return ""
    flat = _flat_tuple_to_type(t, width, elem_kind, elem_width, key_kind, key_width)
    return f" : {flat.render()}"


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
    """One `def Name(p, ...) -> NewType: return { T }` declaration.

    A parameter is either a *type parameter* (unannotated: the call supplies a
    type expression, e.g. `Fixed(int[64], 4)` passes `int[64]` for `t`) or a
    *value parameter* (annotated: the call supplies a literal of that type, e.g.
    `Fixed(n: int[16])` applied as `Fixed(4)` passes the value 4). Both are
    substituted structurally into the body -- the difference is that a value
    argument is checked against its declared type before substitution, so a
    width, capacity or arity can never be handed a type, or vice versa.
    """

    __slots__ = ("name", "params", "value_anns", "body_node")

    def __init__(self, name, params, value_anns, body_node):
        self.name = name
        self.params = params          # list of parameter names, in order
        # Parallel to `params`: the annotation AST for a value parameter, or
        # None for a type parameter.
        self.value_anns = value_anns
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

def _check_value_argument(func_name, param, ann_node, arg):
    """Checks one argument supplied for an annotated (value) parameter.

    The parameter names a scalar value type -- only `int[8|16|32|64]` and
    `bool` -- and the argument must be a literal of exactly that type. A
    value parameter exists so a width, capacity or arity can be threaded
    through a type function as a number rather than as a type, so an argument
    that is not a fitting literal is refused here, before substitution, rather
    than surfacing as a confusing failure to parse the substituted body.
    """
    ann_t = parse_type_annotation(ann_node)
    kind, width = ann_t.kind, ann_t.width
    if ann_t.is_container() or ann_t.kind == "ptr" or kind not in ("int", "bool"):
        raise NotImplementedError(
            f"LITHON-E0132: parameter '{param}' of type function '{func_name}' is "
            f"annotated with '{ast.unparse(ann_node)}'; a value parameter must be a "
            f"scalar -- int[8|16|32|64] or bool (4.8)")
    if kind == "bool":
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, bool)):
            raise NotImplementedError(
                f"LITHON-E0132: parameter '{param}' of type function '{func_name}' is "
                f"bool; its argument must be a bool literal, e.g. True or False (4.8)")
        return
    if isinstance(arg, ast.Constant) and isinstance(arg.value, int) \
            and not isinstance(arg.value, bool):
        value = arg.value
    elif (isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub)
            and isinstance(arg.operand, ast.Constant)
            and isinstance(arg.operand.value, int)
            and not isinstance(arg.operand.value, bool)):
        value = -arg.operand.value
    else:
        raise NotImplementedError(
            f"LITHON-E0132: parameter '{param}' of type function '{func_name}' is "
            f"int[{width}]; its argument must be an integer literal, got "
            f"'{ast.unparse(arg)}' (4.8)")
    _require_supported_width("int", width)
    lo = -(1 << (width - 1))
    hi = (1 << (width - 1)) - 1
    if not (lo <= value <= hi):
        raise NotImplementedError(
            f"LITHON-E0132: argument {value} does not fit int[{width}] for parameter "
            f"'{param}' of type function '{func_name}' (valid range {lo}..{hi}) (4.8)")


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
    for param, ann, arg in zip(tf.params, tf.value_anns, call.args):
        if ann is not None:
            _check_value_argument(name, param, ann, arg)

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
    """Returns a recursive `Type` from an annotation AST node, or raises
    NotImplementedError for forms this frontend slice doesn't handle.
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
            return Type("bool")
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
        # 6.2. `str[N]` is a bounded string: N bytes of NUL-terminated storage.
        # N is a BYTE capacity, not a bit width, so it does not go through
        # _require_supported_width. The element is the byte int[8], which is
        # what makes indexing a string reuse the container byte machinery.
        if base == "str":
            size_node = node.slice
            if not isinstance(size_node, ast.Constant) \
                    or not isinstance(size_node.value, int) \
                    or isinstance(size_node.value, bool):
                raise NotImplementedError(
                    "LITHON-E0111: str[N] requires a literal integer capacity, e.g. str[16]")
            if size_node.value <= 0:
                raise NotImplementedError(
                    f"LITHON-E0111: str capacity must be positive, got {size_node.value}")
            return Type("str", cap=size_node.value, elem=Type("int", width=8))
        if base not in ("int", "float"):
            raise NotImplementedError(f"LITHON-E0109: unknown type '{base}'")
        size_node = node.slice
        if not isinstance(size_node, ast.Constant) or not isinstance(size_node.value, int):
            raise NotImplementedError(f"LITHON-E0109: {base}[N] requires a literal integer size")
        _require_supported_width(base, size_node.value)
        return Type(base, width=size_node.value)

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

    4.3. The value is a full recursive type. A container value is accepted only
    once the tiers can lay one out and index it; until then it is refused here,
    in the one place the type is built, rather than being silently flattened.
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
    key_t = parse_type_annotation(key_node)
    if key_t.is_container() or key_t.kind == "ptr":
        raise NotImplementedError("LITHON-E0112: a dict key cannot itself be a container")
    val_t = parse_type_annotation(val_node)
    if val_t.kind == "dict":
        raise NotImplementedError(
            f"LITHON-E0112: a dict cannot be a dict value (4.3)")
    # 6.9. A value slot is one flat leaf run, so a list or tuple value is the
    # one container shape a slot can hold. A pointer has no leaf run either --
    # it is an address, and its storage is elsewhere -- so it is refused here
    # for the same reason a dict is, rather than silently given a slot.
    if val_t.kind == "ptr":
        raise NotImplementedError(
            f"LITHON-E0112: a pointer cannot be a dict value (4.3)")
    return Type("dict", cap=cap_node.value, elem=val_t, key=key_t)


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
        return Type("ptr", elem=Type("struct", width=structs[slice_node.id].size,
                                    ref=slice_node.id))
    if not isinstance(slice_node, (ast.Name, ast.Subscript)):
        raise NotImplementedError("LITHON-E0113: ptr[T] needs a scalar pointee, e.g. ptr[int[64]] (4.4)")
    elem_t = parse_type_annotation(slice_node)
    # 6.2. A bounded string is also a legal pointee: `s: str[N]` is a real
    # N-byte run in the frame, so `ptr[str[N]]` names it and pointer arithmetic
    # advances by the whole string. valueof() of such a pointer yields the
    # string's run (length, bytes, print), never a scalar -- see the str branch
    # in the addressof lowering, which tracks which str variable a pointer names.
    if elem_t.kind == "str":
        return Type("ptr", elem=elem_t)
    if elem_t.is_container() or elem_t.kind == "ptr":
        raise NotImplementedError(
            f"LITHON-E0113: a ptr pointee is a scalar, not a container (4.4); "
            f"ptr[{elem_t.kind}[...]] does not point at anything single")
    if elem_t.kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"LITHON-E0113: ptr pointees are int, float or bool only, got {elem_t.kind} (4.4)")
    return Type("ptr", elem=elem_t)


def _parse_container_annotation(base, slice_node):
    """Parses the `T, N` of `list[T, N]`.

    The capacity is mandatory and must be a literal integer, because it is the
    thing that makes a list fixed-capacity and heap-free: N decides the frame
    size at compile time. A computed capacity would mean the compiler cannot
    size the allocation, which is the entire reason this type exists, so it is
    rejected rather than deferred.

    4.1. A nested container element is accepted as a TYPE -- the type has to
    exist before the flat-stride work that can lay one out. Together with the
    recursive Type, this is what lets `list[tuple[int[64], 4], 2]` be one value
    instead of being flattened to its outer shell.
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
    elem_t = parse_type_annotation(elem_node)
    if elem_t.is_container():
        # 4.1. A nested list or tuple is a flat leaf run and is accepted. A dict
        # is kept refused: its storage is three regions, so it has no single
        # scalar leaf the flat layout can address.
        if elem_t.kind == "dict":
            raise NotImplementedError(
                f"LITHON-E0112: a dict cannot be an element of {base}; a nested "
                f"dict has no flat scalar leaf to lay out (4.1)")
    return Type(base, cap=cap_node.value, elem=elem_t)


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

# 6.3 Stage A. The bounded run a constant chunk of a printed string literal (or
# f-string) is materialised into before it is printed. The literal has no value
# in this slice, so it is spelled the way any `str[N]` is: a zeroed run plus one
# IndexStore per byte, then the ordinary top-level str print. One run is shared
# by every chunk of the same capacity (the name carries the capacity), because a
# chunk fully overwrites it before printing and a type can be re-declared.
PRINT_LIT_TEMP = "__printlit"

# 6.9. The run a dict's container VALUE is materialised into before the store
# copies it into the table. A container is a run of frame bytes and has no
# scalar form, so the value cannot arrive as a register -- exactly like a str
# key, it arrives as the NAME of a run. One temp per entry: a top-level list
# literal may be shorter than its capacity and leaves the rest zeroed, so a
# shared temp would carry the previous entry's tail into the next one.
DICT_VALUE_TEMP = "__dictval"

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

# 6.10. The closed set of Tier-3 traps a `try` may catch. It mirrors
# `lithon::ir::classify_trap_message`: every name here has a corresponding trap,
# every trap maps to one of these, and anything else (a stack overflow, a
# segfault) is uncatchable. `Exception` is allowed as the catch-all and is not
# in this set -- it is spelled separately, since it names no single trap.
_CATCHABLE_TRAPS = {
    "ZeroDivisionError",
    "OverflowError",
    "ValueError",
    "IndexError",
    "KeyError",
    "EOFError",
}


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

    A field is int[w], float[64], bool, a str[N], a struct, or an enum, each
    declared EARLIER. Requiring earlier declaration is what makes the size of a
    nested aggregate known here and, at the same time, makes a cycle impossible:
    a struct that cannot name itself or a later aggregate has no way to be
    infinitely large. The returned ref_name names the struct or enum a field of
    that kind embeds, and is None for a scalar. For a str[N], `width` carries the
    byte capacity N (there is no bit width) and byte_size is that same N.
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
    ann_t = parse_type_annotation(ann)
    kind, width = ann_t.kind, ann_t.width
    # 6.2. A `str[N]` field is N bytes of bounded-string storage. Like a struct,
    # it is not a value: it has no standalone read, only its bytes (`s.f[i]`),
    # its run-time length, its rendering and its comparison. The capacity N is
    # returned in the `width` slot (for a str's own `Type`, `width` is -1 and
    # `cap` is N; the layout dict records the cap under both names so every
    # reader has it).
    if kind == "str":
        cap = ann_t.cap
        if cap is None or cap <= 0:
            raise NotImplementedError(
                f"LITHON-E0127: field '{field_name}' of struct '{struct_name}' needs a "
                f"positive str capacity, e.g. str[16] (6.2)")
        return "str", cap, None, cap
    if ann_t.is_container() or kind == "ptr":
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
    ann_t = parse_type_annotation(ann)
    kind = ann_t.kind
    if kind == "ptr" or ann_t.is_container():
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
            # 6.2. A str field's byte capacity. For a str `width` already holds
            # it (no scalar bit width applies); this key is the single place the
            # companion-slot code reads it from, so the two can never drift.
            "cap": width if kind == "str" else None,
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
    ann_t = parse_type_annotation(ann)
    kind, width = ann_t.kind, ann_t.width
    if ann_t.is_container() or kind == "ptr":
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

    The signature is plain positional parameter names -- no `/`, `*`, `**` or
    defaults. A parameter is a *type parameter* when unannotated (the call
    supplies a type expression) or a *value parameter* when annotated (the call
    supplies a literal of that scalar type); the annotation is kept and checked
    when the function is applied, because resolving it may name a type function
    declared later. The body is exactly one `return { <type> }` whose braces
    hold one type expression. A unit must bottom out: recursion is refused when
    the function is applied, not here, because a definition alone cannot be
    applied.
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
    value_anns = [arg.annotation for arg in a.args]
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
    registry[name] = TypeFunction(
        name, [arg.arg for arg in a.args], value_anns, val.elts[0])


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
    # 6.2. A pointer to a str walks whole strings: the pointee is a `str[N]`,
    # whose "width" slot carries the byte capacity N, so the stride IS N.
    if kind == "str":
        if width is None or width <= 0:
            raise NotImplementedError(
                f"LITHON-E0113: str needs a stated capacity to be pointed at, e.g. str[16] (6.2)")
        return width
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
        # 6.9. Sequence for the temp run a dict's container value is built into.
        # One per entry: the name is also the type's identity for the checker's
        # "a name has one type" rule, so two dicts with different value types in
        # the same function must not reuse a run.
        self.dictval_counter = 0
        self.var_types = {}
        self.ptr_types = {}
        self.ptr_names = set()
        # 4.4, second half. `ptr[Point]` needs the pointee STRUCT's name, which
        # the IR's `ptr[struct[N]]` deliberately drops (it carries only the byte
        # size). These two maps keep the name beside the size so `valueof(_p).x`
        # can resolve the field layout: ptr_struct_names is keyed by the pointer
        # VARIABLE, ptr_struct by the register a pointer value lives in.
        self.ptr_struct_names = {}
        self.ptr_struct = {}
        # 6.2. A pointer into a bounded string (`_p = addressof(s)`, s: str[N])
        # tracks WHICH string variable it names, so `valueof(_p)` can be
        # lowered to that run's own ops (length, byte read/write, print, ==)
        # instead of loading a scalar. ptr_str_names is keyed by the pointer
        # VARIABLE, ptr_str by the register a pointer value lives in -- the
        # mirror of the ptr_struct pair above.
        self.ptr_str_names = {}
        self.ptr_str = {}
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
        # Phase 6.7. The run-time length field. A list is a fixed-capacity run
        # of leaves, but it also carries a companion `int[64]` variable (named
        # `<list>.__len`) holding HOW MANY of those slots are live. Declaring a
        # list always emits it; `list.append` and `str.split` advance it, `len`
        # reads it, and print/reduction consult it so trailing zeroed slots are
        # not mistaken for elements. `list_len` records what the frontend can
        # prove about that value: an int when it is a compile-time constant (the
        # common case -- a fixed literal), or None once an operation could make
        # it vary at run time (an append inside a loop, a split). A reduction
        # over a list whose length is only known at run time is refused rather
        # than silently reading the capacity.
        self.list_len = {}
        # Phase 6.7. How many enclosing loops the builder is currently inside.
        # An `append` (or a store that could extend a list) seen while this is
        # non-zero can run a varying number of times, so the list's length stops
        # being a compile-time constant.
        self.loop_depth = 0
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
        # Phase 6.7. A list's scan covers its LIVE length, not its capacity --
        # otherwise a trailing zeroed slot would answer `0 in xs` for free. The
        # length is a compile-time constant unless the list can be appended to
        # in a loop or split, in which case the unrolled scan has no fixed bound
        # and is refused rather than silently scanning the whole run.
        cdecl = self.declared[container_node.id]
        if cdecl[0] == "list":
            live = self._list_live_length(container_node.id)
            if live is None:
                raise NotImplementedError(
                    f"LITHON-E0114: `in` over `{container_node.id}` -- its length is only "
                    f"known at run time (it is appended to or split), and `in` unrolls "
                    f"over a compile-time length (6.7)")
            cap = live
        # The element comparison is `eq`. It takes int, float and bool operands
        # in both tiers (a bool compares as its 0/1 value, matching Python), so
        # a list of any scalar may be scanned. Anything else -- a str or an
        # aggregate element -- has no scalar `eq`, so it is refused here, where
        # the source names the list, rather than failing later inside a tier.
        elem_kind = self.declared[container_node.id].elem.kind
        if elem_kind not in ("int", "float", "bool"):
            raise NotImplementedError(
                f"LITHON-E0114: `in` over a list of {elem_kind} is not supported -- it "
                f"lowers to `==` over the elements, and equality only takes int, float "
                f"or bool scalars")
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
                if node.id in self.ptr_struct_names:
                    self.ptr_struct[r] = self.ptr_struct_names[node.id]
                if node.id in self.ptr_str_names:
                    self.ptr_str[r] = self.ptr_str_names[node.id]
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
                        if left in self.ptr_struct:
                            self.ptr_struct[r] = self.ptr_struct[left]
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
            # 6.2. `s == t` between two bounded strings is a run comparison, not
            # a scalar `eq`: a string is N bytes and never a register value, so
            # it lowers to StrEq naming both places rather than loading either.
            # A str FIELD (`s.f == t.f`, or a field against a top-level string)
            # is such a place too, naming its companion run. Both operands are
            # resolved by a pure walk (no emission), so source order holds.
            if op_type is ast.Eq or op_type is ast.NotEq:
                ln = self._str_place_name(node.left)
                rn = self._str_place_name(node.comparators[0])
                if ln is not None and rn is not None:
                    r = self.new_reg()
                    self.emit(f"{r} = {'streq' if op_type is ast.Eq else 'strne'} "
                              f"{ln}, {rn}")
                    return r
            left = self.build_expr(node.left)
            right = self.build_expr(node.comparators[0])
            op_map = {ast.Lt: "lt", ast.Gt: "gt", ast.Eq: "eq", ast.NotEq: "ne"}
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
                # 6.4. `s.find(sub)` / `s.count(sub)` on a bounded string. A str
                # has no methods in the 4.9 sense, so these lower to a two-name
                # substring scan before the method inliner is consulted. The
                # helper returns None when the receiver is not a str place, so a
                # struct method named find/count still reaches the inliner.
                if node.func.attr in ("find", "count"):
                    r = self._build_str_method(node)
                    if r is not None:
                        return r
                # Phase 6.7. `xs.append(v)` as a value: Python's append returns
                # None, so using it as a value is refused below.
                if node.func.attr == "append" and self._build_list_append(node):
                    raise NotImplementedError(
                        "LITHON-E0135: list.append() returns no value, so it cannot be "
                        "used as a value -- write it as its own statement (6.7)")
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
                if len(node.args) != 1:
                    raise NotImplementedError("LITHON-E0115: len() takes exactly one variable")
                # 6.2. A string has a RUN-TIME length (the bytes before the NUL),
                # unlike a container whose len() is the compile-time capacity, so
                # it takes a different opcode rather than a folded constant. A
                # str field (`len(s.f)`) is such a place too, via its companion run.
                sp = self._str_place_name(node.args[0])
                if sp is not None:
                    r = self.new_reg()
                    self.emit(f"{r} = strlen {sp}")
                    return r
                if not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError("LITHON-E0115: len() takes exactly one variable")
                # Phase 6.7. A list's len() is the LIVE length, not the
                # capacity: a shorter literal (or a sequence of appends) leaves
                # trailing slots unused. It is carried in the list's companion
                # length variable, so this is a plain load -- no runtime helper
                # and no constant folding that a later append could invalidate.
                arg_name = node.args[0].id
                decl = self.declared.get(arg_name)
                if decl is not None and decl[0] == "list":
                    r = self.new_reg()
                    self.emit(f"{r} = load {self._list_len_name(arg_name)}")
                    return r
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
                dict_name = node.args[0].id
                key_operand = self._dict_key_operand(dict_name, node.args[1])
                r = self.new_reg()
                self.emit(f"{r} = DictContains {dict_name}, {key_operand}")
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
                # 6.2. valueof() of a ptr[str[N]] denotes the WHOLE bounded
                # string, which -- like every string -- has no value. It is only
                # meaningful where a string place is expected (printed, len'd,
                # indexed, compared), and those spellings resolve the
                # pointer's str-variable name directly rather than loading a
                # scalar. Reaching here means it was used in a value position.
                if k == "str":
                    raise NotImplementedError(
                        "LITHON-E0114: valueof() of a ptr[str[N]] is the whole string, which has "
                        "no value -- use it as a place, e.g. print(valueof(_p)), "
                        "len(valueof(_p)) or valueof(_p)[i] (6.2)")
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
                    self.ptr_struct[r] = self.struct_vars[name]
                    return r
                if name in self.var_types:
                    r = self.new_reg()
                    self.emit(f"{r} = addressof {name}")
                    self.ptr_types[r] = self.var_types[name]
                    return r
                # 6.2. A str[N] is a real N-byte run in the frame, so it can be
                # pointed at. The pointer carries the capacity as its stride
                # (`_p + 1` walks one whole string) and, separately, the name of
                # the string it names, so `valueof(_p)` can be lowered to that
                # run's own ops rather than a bogus scalar load.
                decl = self.declared.get(name)
                if decl is not None and decl.kind == "str":
                    r = self.new_reg()
                    self.emit(f"{r} = addressof {name}")
                    self.ptr_types[r] = ("str", decl.cap)
                    self.ptr_str[r] = name
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
                # fallible wrapper to unwrap here. Arguments are int[64] or
                # pointers (5.2 needs `&sockaddr_in` and send/recv buffers); a
                # pointer is a real host address in both tiers (4.4). Floats and
                # aggregates are refused by the checker. A user function named
                # `syscall` would collide and is rejected by the checker.
                if not (1 <= len(node.args) <= 7):
                    raise NotImplementedError(
                        "LITHON-E0115: syscall() takes a syscall number and up to six "
                        "arguments, e.g. syscall(102) (Phase III)")
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
            # 4.1 (phase 3). `xs[i][j]...` are all subscripts of one variable,
            # so the chain is collected into ONE multi-index Index. The AST nests
            # left-deep (the innermost `xs` is reached last), so the slices are
            # reversed into source order before being emitted.
            slices = []
            cur = node
            while isinstance(cur, ast.Subscript):
                slices.append(cur.slice)
                cur = cur.value
            if not isinstance(cur, ast.Name):
                # 6.2. A string PLACE can be indexed as its bytes even though a
                # str has no value: `s.f[i]` (a struct field's companion run) and
                # `valueof(_p)[i]` (the str a pointer names). Both resolve to the
                # variable the run lives in, so the ordinary one-level Index is
                # emitted. Two levels are refused -- a str's bytes are already
                # the leaf.
                place = self._str_place_name(cur)
                if place is not None and len(slices) == 1:
                    slices.reverse()
                    idx = self.build_expr(slices[0])
                    r = self.new_reg()
                    self.emit(f"{r} = Index {place}, {idx}")
                    return r
                raise NotImplementedError(
                    "LITHON-E0117: only a plain variable can be indexed, e.g. xs[i]; there is no "
                    "value that denotes a container yet")
            slices.reverse()
            r = self.new_reg()
            # 4.3. A dict subscript is a hash and a probe, not an address
            # computation, so it is a different opcode. Its key is resolved
            # separately: an int/bool key is a register, a str key is the NAME of
            # the string run.
            #
            # 6.9. A dict whose VALUE is a container adds one step: the probe
            # copies the bucket's leaves into a fresh run named here, and the
            # read is then an ordinary list/tuple subscript of that run. That is
            # what lets `t[k]` and `t[k][i][j]` both read the same value -- the
            # run is the value, so a deeper chain just keeps indexing it.
            if cur.id in self.dict_names:
                key_operand = self._dict_key_operand(cur.id, slices[0])
                val_t = self.declared[cur.id].elem
                if val_t.kind in ("list", "tuple"):
                    self.dictval_counter += 1
                    run = f"{DICT_VALUE_TEMP}{self.dictval_counter}"
                    # The run is declared first so both tiers allocate it exactly
                    # as they would a list literal's run; the read then overwrites
                    # every leaf of it.
                    self.emit(f"store {run}{render_type_suffix(val_t)}")
                    self.emit(f"DictIndex {cur.id}, {key_operand}, {run}"
                              f"{render_type_suffix(val_t)}")
                    self.declared[run] = val_t
                    # A list prints its LIVE length, so the run needs the same
                    # companion a list literal gets. A tuple has none -- it prints
                    # its whole declared run -- which is why this is only for a
                    # list value. The whole run was stored, so every top-level slot
                    # is live: the companion starts at the value type's capacity.
                    if val_t.kind == "list":
                        self._declare_list_length(run, val_t.cap, None)
                    rest = slices[1:]
                    if not rest:
                        return run
                    idxs = [self.build_expr(s) for s in rest]
                    rr = self.new_reg()
                    self.emit(f"{rr} = Index {run}, {', '.join(idxs)}")
                    return rr
                if len(slices) != 1:
                    raise NotImplementedError(
                        f"LITHON-E0117: '{cur.id}[...]' has more than one subscript but its "
                        f"value type is {val_t.render()}, which is not a container")
                self.emit(f"{r} = DictIndex {cur.id}, {key_operand}")
                return r
            idxs = [self.build_expr(s) for s in slices]
            self.emit(f"{r} = Index {cur.id}, {', '.join(idxs)}")
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
            pf = self._resolve_ptr_field(node)
            if pf is not None:
                reg, offset, kind, width, _ = pf
                r = self.new_reg()
                self.emit(f"{r} = fieldload {reg}, {offset}{render_type_suffix(kind, width)}")
                return r
            var, offset, kind, width, synth = self._resolve_struct_field(node)
            if kind == "str":
                # 6.2. A whole str field has no value (like a nested container or
                # a struct): only `s.f[i]`/`len(s.f)`/`print(s.f)`/`s.f == t.f`
                # reach it. `x = s.f` would need a string value, which the
                # language does not have.
                raise NotImplementedError(
                    f"LITHON-E0114: '{node.attr}' is a {render_type_suffix(Type('str', cap=width)).lstrip(' :')} "
                    f"field with no value -- index a byte (`{node.attr}`[i]), take its len(), "
                    f"print it, or compare it (6.2)")
            r = self.new_reg()
            self.emit(f"{r} = fieldload {var}, {offset}{render_type_suffix(kind, width)}")
            return r

        raise NotImplementedError(f"LITHON-E0114: expression node {type(node).__name__} not supported yet")

    def _resolve_ptr_field(self, node):
        """`valueof(_p).field` -- a field read THROUGH a pointer.

        4.4, second half. The base is a `valueof` call over a pointer whose
        pointee is a named struct, so the field's byte offset is resolved from
        that struct's layout exactly as the named form does. The only difference
        is the SUBJECT handed to fieldload: a register holding the pointer,
        rather than a struct variable's name. Returns (register, offset, kind,
        width, None), or None when the chain does not start from `valueof`. The
        trailing None is the str companion-slot name every field resolver
        carries; a str field is refused here, so this form never has one.
        """
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name)
                and cur.func.id == "valueof"):
            return None
        if len(cur.args) != 1:
            raise NotImplementedError(
                "LITHON-E0116: valueof() takes exactly one pointer, e.g. valueof(_p) (4.4)")
        reg = self.build_expr(cur.args[0])
        if reg not in self.ptr_types:
            raise NotImplementedError(
                "LITHON-E0116: valueof() needs a pointer, e.g. valueof(_p) (4.4)")
        if reg not in self.ptr_struct:
            raise NotImplementedError(
                "LITHON-E0128: valueof(...).field needs a pointer whose pointee is a named "
                "struct with a known layout (4.4)")
        parts.reverse()
        layout = self.structs[self.ptr_struct[reg]]
        offset = 0
        kind = width = None
        for i, field_name in enumerate(parts):
            field = layout.fields.get(field_name)
            if field is None:
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
                    f"LITHON-E0131: '{field_name}' is an enum field and has no value -- read it "
                    f"with a `match` (4.7)")
            if field["kind"] == "str":
                # 6.2. A str field's bytes live in a companion slot keyed by the
                # struct VARIABLE, which an address alone cannot name, so a str
                # field is not reachable through `valueof(_p).field`. Reading the
                # struct's raw bytes here would see the reserved-but-unused run,
                # not the string.
                raise NotImplementedError(
                    f"LITHON-E0128: '{field_name}' is a str field; a bounded string field is "
                    f"reached through its struct variable (e.g. s.{field_name}), not through a "
                    f"pointer into the struct (6.2)")
            if not last:
                raise NotImplementedError(
                    f"LITHON-E0128: '{field_name}' is a {field['kind']}, not a struct, so it "
                    f"has no fields (4.6)")
            kind, width = field["kind"], field["width"]
        return reg, offset, kind, width, None

    def _resolve_struct_field(self, node):
        """Resolves an attribute chain `a.b.c...` to (variable, byte_offset,
        leaf_kind, leaf_width, str_place), or raises the E0128 refusal that
        explains why it is not a field access.

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
        # 6.2. A str field's bytes are held in a companion slot named by the
        # full attribute path (`s.a`, `s.inner.a`), declared alongside the struct
        # variable and keyed off it. The five ops on a bounded string -- byte
        # read/write, print, len and == -- all name that slot, so no new IR
        # opcode is needed and both tiers reuse the machinery a top-level
        # `str[N]` already uses.
        synth = None
        if kind == "str":
            synth = var + "." + ".".join(parts)
        return var, offset, kind, width, synth

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
        self.declared[name] = Type(kind, width=width)
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
        self.loop_depth += 1
        for stmt in body:
            self.build_stmt(stmt)
        self.loop_depth -= 1

    # 6.10. try/except over the built-in Tier-3 traps. The whole construct is
    # ordinary control flow plus three markers: TryEnter opens a protected
    # region and names the handlers, TryLeave closes it on normal completion,
    # and each handler block begins with TryCatch. Neither tier builds an
    # exception object or unwinds a stack -- a caught trap is a jump.
    #
    # Layout:
    #
    #     tryenter <kinds>, <handler labels>
    #     <body>
    #   [ tryleave ; <else> ]                 # else runs only on normal completion
    #     jump <finally-or-merge>
    #   h1: trycatch <E1>  <H1>  jump <finally-or-merge>
    #   h2: trycatch <E2,E3> <H2> jump <finally-or-merge>
    #   finally: <FIN>
    #   merge:
    #
    # `finally` is duplicated onto the normal path and onto the end of every
    # handler, so it runs whichever way the try finished -- except when a trap
    # is left UNCAUGHT, in which case the program still terminates (there is no
    # `raise` in this slice, so there is nothing to re-raise with). That
    # narrowing is recorded in the README's divergence table.
    def build_try(self, node):
        if node.finalbody and not node.handlers and not node.orelse:
            raise NotImplementedError(
                "LITHON-E0114: `try`/`finally` with no `except` is not supported yet -- "
                "a `finally` here would have to run on the way out of an uncaught trap, "
                "which needs `raise` (6.10)")
        handlers = []
        for h in node.handlers:
            if h.type is None:
                raise NotImplementedError(
                    "LITHON-E0114: a bare `except:` is not supported -- it also catches "
                    "system-exit, and here it would suggest catching a segfault, which is "
                    "not catchable; name the trap (`except Exception:` catches every "
                    "catchable trap) (6.10)")
            if h.name is not None:
                raise NotImplementedError(
                    "LITHON-E0114: `except ... as e` is not supported yet -- there is no "
                    "exception object to bind (6.10)")
            kinds = self._except_kinds(h.type)
            label = self.reserve_label()
            # A trap jump targets this label, but it is named inside TryEnter
            # rather than by a `jump`, so mark it reachable here.
            self.reachable_labels.add(label)
            handlers.append((kinds, label, h.body))

        body_label = self.reserve_label()
        else_label = self.reserve_label() if node.orelse else None
        finally_label = self.reserve_label() if node.finalbody else None
        merge_label = self.reserve_label()
        after_label = finally_label if finally_label else merge_label

        # TryEnter names every kind and its handler label, positionally: each
        # handler label appears once per KIND it catches, so a `(A, B)` handler
        # is reachable from either name. `+` joins the lists because the IR text
        # parser splits arguments on commas.
        flat_kinds = "+".join(k for kinds, _, _ in handlers for k in kinds)
        flat_labels = "+".join(
            label for kinds, label, _ in handlers for _ in kinds)
        self.emit(f"tryenter {flat_kinds}, {flat_labels}")
        self.emit(f"jump {body_label}")

        self.start_block(body_label)
        for stmt in node.body:
            self.build_stmt(stmt)
        if node.orelse:
            self.emit("tryleave")
            self.emit(f"jump {else_label}")
            self.start_block(else_label)
            for stmt in node.orelse:
                self.build_stmt(stmt)
            self.emit(f"jump {after_label}")
        else:
            self.emit("tryleave")
            self.emit(f"jump {after_label}")

        for kinds, label, body in handlers:
            self.start_block(label)
            # The handler's own kind list, for the reader of the IR; a trap
            # already knows its target label from TryEnter, so this is a marker
            # and not a lookup.
            self.emit(f"trycatch {'+'.join(kinds)}")
            for stmt in body:
                self.build_stmt(stmt)
            self.emit(f"jump {after_label}")

        if node.finalbody:
            self.start_block(finally_label)
            for stmt in node.finalbody:
                self.build_stmt(stmt)
            self.emit(f"jump {merge_label}")

        self.start_block(merge_label)

    def _except_kinds(self, type_node):
        """The exception names an `except` clause names, as a list.

        `except ValueError:` -> ["ValueError"]; `except (A, B):` -> ["A", "B"];
        `except Exception:` -> ["Exception"] (the catch-all). A dotted or
        aliased name, or a non-name expression, is refused: the set is closed.
        """
        if isinstance(type_node, ast.Tuple):
            return [self._except_name(e) for e in type_node.elts]
        return [self._except_name(type_node)]

    def _except_name(self, node):
        if isinstance(node, ast.Name):
            name = node.id
            if name in _CATCHABLE_TRAPS or name == "Exception":
                return name
            raise NotImplementedError(
                f"LITHON-E0114: `except {name}:` is not a catchable trap -- the catchable "
                f"set is {', '.join(sorted(_CATCHABLE_TRAPS))}, plus `Exception` for all "
                f"of them (6.10)")
        raise NotImplementedError(
            "LITHON-E0114: an `except` clause must name a built-in trap (6.10)")

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
        self.loop_depth += 1
        for stmt in node.body:
            self.build_stmt(stmt)
        self.loop_depth -= 1
        self.emit(f"jump {merge_label}")

        if node.orelse:
            self.start_block(else_label)
            self.loop_depth += 1
            for stmt in node.orelse:
                self.build_stmt(stmt)
            self.loop_depth -= 1
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
        self.loop_depth += 1
        for stmt in node.body:
            self.build_stmt(stmt)
        self.loop_depth -= 1
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

    def _list_len_name(self, name):
        """The companion `int[64]` variable holding a list's live length."""
        return name + ".__len"

    def _list_live_length(self, name):
        """The compile-time live length of list `name`, or None if it is only
        known at run time (Phase 6.7).

        A list's capacity is fixed at declaration, but how many slots are live
        is a run-time value once the list can be appended to or split. The
        frontend tracks the constant case exactly (a fixed literal, or a
        straight-line append) and gives up -- None -- the moment the count could
        vary. Callers use this to fold `len`, a reduction or a membership scan,
        and refuse (rather than read the capacity) when it is None.
        """
        return self.list_len.get(name)

    def _container_print_arg(self, arg):
        """(variable-name, declared Type) of a container or bounded string named
        directly in a print argument, or None when the argument is not one.

        6.1. Only a bare name can be printed as a whole aggregate: a container
        has no value, so `print(xs)` is the one spelling that names one. An
        indexed element is a scalar and takes the ordinary print path.

        6.2. A `str[N]` field is the second place a whole bounded string can be
        printed (`print(s.f)`): it is named by its companion run, whose capacity
        is the field's own.
        """
        if isinstance(arg, ast.Name):
            decl = self.declared.get(arg.id)
            if decl is not None and decl.kind in ("list", "tuple", "dict", "str"):
                return arg.id, decl
            return None
        if isinstance(arg, ast.Attribute):
            try:
                _var, _off, kind, width, synth = self._resolve_struct_field(arg)
            except NotImplementedError:
                return None
            if kind == "str":
                return synth, Type("str", cap=width)
        # 6.2. `print(valueof(_p))` names the whole string the pointer points at,
        # exactly as `print(s)` names s. It resolves to the string variable's
        # own companion run, so it prints through the same top-level str path
        # (bare text, no quotes -- it is not nested here).
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) \
                and arg.func.id == "valueof" and len(arg.args) == 1 \
                and isinstance(arg.args[0], ast.Name):
            place = self.ptr_str_names.get(arg.args[0].id)
            if place is not None:
                return place, self.declared.get(place)
        return None

    # ---- 6.3 Stage A. Printing string literals and f-strings ---------------
    #
    # `print("literal")`, `print("x is:", x)` and `print(f"x is {x}")` all lower
    # to the SAME runtime pieces an ordinary multi-argument print already uses:
    # a value printer, a separator and a newline. The new work is the constant
    # chunks. A chunk is materialised into a shared bounded run (`str[L+1]`) and
    # printed through the existing top-level str path, so both tiers render it
    # exactly as `print(s)` renders a `str[N]` -- bare text, no quotes, the same
    # bytes a `str[N]` literal construction writes. No format string carrying
    # user text is ever handed to printf, so a `%` in a literal is just a byte.

    def _emit_print_arg(self, arg):
        """One top-level print() argument, expanded in place.

        A string literal or an f-string expands to its pieces with NO separator
        between them (f"a{b}" is "ab", not "a b"); every other argument is a
        single value or aggregate. The caller emits the separator between
        top-level arguments, which is exactly Python's rule.
        """
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self._emit_print_literal(arg.value)
            return
        if isinstance(arg, ast.JoinedStr):
            self._emit_print_fstring(arg)
            return
        self._emit_print_value(arg)

    def _emit_print_value(self, arg):
        """A single argument: an aggregate/bounded-string place, or a scalar.

        Evaluation and printing are one step here, which is what an ordinary
        `print(x)` wants. An f-string cannot use this: it has to evaluate every
        `{expr}` BEFORE it prints any piece, so it splits the two halves and uses
        them separately (see _emit_print_fstring).
        """
        self._print_place(self._eval_print_place(arg))

    def _eval_print_place(self, arg):
        """Evaluates one print operand and returns where its text lives.

        6.9. The returned place is one of:
          ("container", name, decl) -- a whole aggregate/bounded string, printed
             from its own run by name;
          ("reg", register)         -- a scalar, or a container value read out of
             a dict into the run named by `register`.
        Only the code that MATERIALISES the value is emitted, so a caller can
        evaluate several operands in Python's argument order and print them
        afterwards -- which is exactly what an f-string's `{...}` parts need.
        """
        cp = self._container_print_arg(arg)
        if cp is not None:
            return ("container", cp[0], cp[1])
        arg_reg = self.build_expr(arg)
        # 6.9. A dict value that is a container is read out into a run, so
        # `print(d[k])` names a run rather than a register. It prints its whole
        # run, exactly as `print(d)` renders the same value nested.
        decl = self.declared.get(arg_reg)
        if decl is not None and decl.kind in ("list", "tuple"):
            return ("container", arg_reg, decl)
        return ("reg", arg_reg)

    def _print_place(self, place):
        """Emits the runtime print for a place _eval_print_place produced."""
        kind, value, *rest = place
        if kind == "container":
            decl = rest[0]
            # Phase 6.7. A list prints its LIVE length, so the companion length
            # variable rides along as the second name. Tuples, dicts and strings
            # have no length field and print their whole run as before.
            if decl[0] == "list":
                self.emit(f"print_container {value}, "
                          f"{self._list_len_name(value)}{render_type_suffix(decl)}")
                return
            self.emit(f"print_container {value}{render_type_suffix(decl)}")
            return
        self.emit(f"call print_bare, {value}")

    def _emit_print_fstring(self, node):
        """A `JoinedStr`: literal chunks verbatim, `{expr}` via the value path.

        `{{` / `}}` are already decoded to single braces by CPython's parser. A
        format spec (`{x:.2f}`), a conversion (`{x!r}`) and `{x=}` are refused
        rather than silently printed wrong; floats already use the one shared
        shortest-round-trip formatter, so the plain `{x}` is exact.

        Python builds the whole f-string value before it prints anything: every
        `{expr}` is evaluated, the pieces are concatenated, and only then is the
        result written out. That ordering is observable whenever an expression
        has a side effect, e.g. `f"{f()}"` where `f` itself prints -- CPython
        runs `f` first and prints `a` and `b` around the value it returned, while
        emitting the literal chunks as we reach them would write "a" before `f`
        ran. So every `{expr}` is evaluated FIRST, in source order, into a place
        (register or its own run), and the pieces are printed only after. A
        literal chunk needs no stashing: it is materialised fresh at print time.
        """
        pieces = []   # ("literal", text) | ("place", place)
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces.append(("literal", part.value))
                continue
            if isinstance(part, ast.FormattedValue):
                if part.format_spec is not None:
                    raise NotImplementedError(
                        "LITHON-E0114: an f-string format specifier (`{x:.2f}`) is not "
                        "supported yet (6.3)")
                if part.conversion != -1:
                    raise NotImplementedError(
                        "LITHON-E0114: an f-string conversion (`{x!r}`) is not supported "
                        "yet (6.3)")
                pieces.append(("place", self._eval_print_place(part.value)))
                continue
            raise NotImplementedError(
                f"LITHON-E0114: unsupported f-string part {type(part).__name__} (6.3)")
        for kind, payload in pieces:
            if kind == "literal":
                self._emit_print_literal(payload)
            else:
                self._print_place(payload)

    def _materialize_str_literal(self, text):
        """Emits a constant string chunk into a shared bounded run; returns
        (run-name, capacity).

        The run is `str[L+1]`, one per capacity (the name carries the capacity),
        zeroed by the valueless store and filled by one IndexStore per UTF-8
        byte, so the NUL terminator after the last byte is implicit. `L+1` is the
        smallest capacity the literal fits, since a literal of `L` bytes needs
        `L < N`. Sharing one run per capacity is safe: a chunk fully overwrites
        it before it is read. Used by 6.3 (a printed literal chunk) and 6.4 (a
        substring scan's literal needle).
        """
        data = text.encode("utf-8")
        cap = len(data) + 1
        temp = f"{PRINT_LIT_TEMP}{cap}"
        self.emit(f"store {temp} : str[{cap}]")
        for i, byte in enumerate(data):
            idx = self.new_reg()
            self.emit(f"{idx} = const_i64 {i}")
            val = self.new_reg()
            self.emit(f"{val} = const_i64 {byte}")
            self.emit(f"IndexStore {temp}, {idx}, {val}")
        return temp, cap

    def _materialize_container_value(self, val_t, val_node, entry_index):
        """Emits a dict's container VALUE into its own run; returns the run name.

        6.9. A dict value may be a list or a tuple (4.3), and a container is a
        run of frame bytes with no scalar form to put in a register -- the same
        reason a str key is named rather than loaded. So the literal is built
        into a fresh `store __dictvalK : T` (which also zeroes it, which is what
        a top-level list literal shorter than its capacity relies on) and the
        name is what the store carries.

        The name is taken from a counter rather than shared the way a str chunk
        is, because a run's TYPE is its identity for the checker's "a name has
        one type" rule: two dicts with different value types in one function
        would otherwise declare the same name twice with two shapes. Overwriting
        rather than reusing is also what keeps an entry whose literal is shorter
        than its capacity from inheriting the previous entry's tail.
        """
        self.dictval_counter += 1
        temp = f"{DICT_VALUE_TEMP}{self.dictval_counter}"
        self.emit(f"store {temp}{render_type_suffix(val_t)}")
        self._build_container_literal(temp, val_t, val_node, [])
        return temp

    def _emit_print_literal(self, text):
        """Prints a constant chunk without ever building a string value.

        The bytes go into a shared `str[L+1]` run and out through the top-level
        str print path. An empty chunk emits nothing -- the surrounding
        print_sep / print_nl pieces already spell Python's output for it.
        """
        if not text:
            return
        temp, cap = self._materialize_str_literal(text)
        self.emit(f"print_container {temp} : str[{cap}]")

    def _build_str_method(self, call):
        """`s.find(sub)` / `s.count(sub)` (6.4), or None when the receiver is
        not a bounded-string place (so a struct method that happens to be named
        find/count still falls through to the 4.9 inliner).

        A str has no methods in the 4.9 sense -- there is no receiver object to
        pass -- so both are two-name scans, like `s == t`: the haystack is the
        receiver's run and the needle is either another str place or a string
        literal materialised into its own run. find returns the lowest index or
        -1; count returns the number of non-overlapping matches. The result is a
        full int[64], matching Python and `len(s)`.
        """
        recv = call.func.value
        hay = self._str_place_name(recv)
        if hay is None:
            return None
        if len(call.args) != 1 or call.keywords:
            raise NotImplementedError(
                f"LITHON-E0114: str.{call.func.attr}() takes exactly one string "
                f"argument, e.g. s.{call.func.attr}(sub) (6.4)")
        sub = call.args[0]
        needle = self._str_place_name(sub)
        if needle is None:
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                needle, _cap = self._materialize_str_literal(sub.value)
            else:
                raise NotImplementedError(
                    f"LITHON-E0114: str.{call.func.attr}() needs a string literal or a "
                    f"str variable as its argument (6.4)")
        r = self.new_reg()
        op = "strfind" if call.func.attr == "find" else "strcount"
        self.emit(f"{r} = {op} {hay}, {needle}")
        return r

    def _build_list_append(self, call):
        """`xs.append(v)` on a fixed-capacity list (Phase 6.7).

        Returns True when the receiver is a plain list variable, else False so
        a struct method named `append` still reaches the 4.9 inliner.

        A list is a fixed run of leaves plus a live-length companion. Appending
        is: read the length, write the new value into that slot (the ordinary
        `IndexStore`, whose runtime bounds check is what traps when the run is
        full), then advance the length. No new opcode -- it is exactly the
        `xs[i] = v` the source would write by hand, plus the count bump.
        """
        recv = call.func.value
        if not isinstance(recv, ast.Name):
            return False
        name = recv.id
        decl = self.declared.get(name)
        if decl is None or decl[0] != "list":
            return False
        if len(call.args) != 1 or call.keywords:
            raise NotImplementedError(
                "LITHON-E0114: list.append() takes exactly one value, e.g. xs.append(v) (6.7)")
        cap = decl[1]
        live = self._list_live_length(name)
        if live is not None and live >= cap:
            raise NotImplementedError(
                f"LITHON-E0111: `{name}` is already full (its capacity is {cap}), so "
                f"append() would overflow it -- declare it with more room (6.7)")
        len_name = self._list_len_name(name)
        idx = self.new_reg()
        self.emit(f"{idx} = load {len_name}")
        val = self.build_expr(call.args[0])
        # The bounds check on a runtime index traps "list index out of range"
        # when idx == cap, which is exactly a full list -- the two tiers already
        # agree on that text, so append inherits it rather than adding a new trap.
        self.emit(f"IndexStore {name}, {idx}, {val}")
        one = self.new_reg()
        self.emit(f"{one} = const_i64 1")
        nxt = self.new_reg()
        self.emit(f"{nxt} = add {idx}, {one}")
        self.emit(f"store {len_name}, {nxt}")
        # The compile-time length is still exact for a straight-line append;
        # inside a loop (or any control flow that can run it more than once)
        # the count becomes a run-time value and any compile-time reasoning
        # about the list's length is given up.
        if self.loop_depth == 0 and live is not None:
            self.list_len[name] = live + 1
        else:
            self.list_len[name] = None
        return True

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
            # Phase 6.7. The loop bound becomes a literal `range(...)` limit, so
            # it must be a compile-time constant. A list whose live length is
            # run-time (appended to in a loop, or split) has no such bound, so
            # iterating it with enumerate/zip is refused here.
            adecl = self.declared.get(arg.id)
            if adecl is not None and adecl[0] == "list":
                live = self._list_live_length(arg.id)
                if live is None:
                    raise NotImplementedError(
                        f"LITHON-E0119: {fname}({arg.id}) -- {arg.id}'s length is only known "
                        f"at run time (it is appended to or split), and {fname} needs a "
                        f"compile-time bound (6.7)")
                cap = live
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
        decl_key = Type(kind, width=width)
        prev_decl = self.declared.get(name)
        if prev_decl is not None and prev_decl != decl_key:
            def spell(d):
                return render_type_suffix(d).lstrip(" :") or "?"
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

        # Phase 6.7. The walk is over the list's LIVE length, not its capacity.
        # For a fixed literal that is still a compile-time constant (so the
        # unrolled walk is unchanged); once the list can be appended to in a
        # loop or split, the bound is a run-time value and there is no fixed
        # unroll to emit, so the reduction is refused here.
        if kind == "list":
            live = self._list_live_length(name)
            if live is None:
                raise NotImplementedError(
                    f"LITHON-E0115: {fname}({name}) -- {name}'s length is only known at "
                    f"run time (it is appended to or split), and {fname} unrolls over a "
                    f"compile-time length (6.7)")
            cap = live

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
            self.loop_depth += 1
            for stmt in node.body:
                self.build_stmt(stmt)
            self.loop_depth -= 1
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
        self.loop_depth += 1
        for stmt in node.body:
            self.build_stmt(stmt)
        self.loop_depth -= 1
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

    def _build_container_literal(self, name, decl_t, value_node, prefix):
        """Recursively fills a (possibly nested) list/tuple declaration.

        4.1 (phase 3). `prefix` is the chain of index value-registers already
        materialised for the enclosing levels, so a SCALAR leaf emits one
        multi-index IndexStore whose arguments are prefix + [i]. A nested element
        recurses with prefix + [i]. This is the same IndexStore an ordinary
        `xs[i][j] = v` uses -- nesting gets no second constructor opcode, so the
        tuple immutability rule still has exactly one place to live (the type
        checker recognises the valueless declaration followed by its leaf stores).
        """
        kind = decl_t.kind
        cap = decl_t.cap
        lit = ast.Tuple if kind == "tuple" else ast.List
        lit_spelling = "(a, b, ...)" if kind == "tuple" else "[a, b, ...]"
        if not isinstance(value_node, lit):
            raise NotImplementedError(
                f"LITHON-E0123: a {kind} is built from a {kind} literal, so `{name}: ... = "
                f"<expr>` needs {lit_spelling}, not a single expression (4.1)")
        if len(value_node.elts) != cap:
            # Phase 6.7. A TOP-LEVEL list literal may be shorter than the
            # capacity: the extra slots stay zeroed and simply are not live, so
            # `list.append` has somewhere to put the next element and `len`
            # reports the number written. A tuple is immutable and has no
            # length field, and a NESTED list's inner count is not tracked, so
            # both still require their literal to fill the run exactly.
            if kind == "list" and not prefix and len(value_node.elts) < cap:
                pass
            else:
                raise NotImplementedError(
                    f"LITHON-E0123: `{name}: {kind}[..., {cap}]` needs exactly {cap} element"
                    f"{'' if cap == 1 else 's'}, got {len(value_node.elts)} (4.1)")
        elem_t = decl_t.elem
        for i, elt in enumerate(value_node.elts):
            idx_reg = self.new_reg()
            self.emit(f"{idx_reg} = const_i64 {i}")
            chain = prefix + [idx_reg]
            if elem_t.is_container():
                self._build_container_literal(name, elem_t, elt, chain)
                continue
            # 6.2. A str element is a run of byte leaves, so it is filled exactly
            # like the str declaration itself: one IndexStore per content byte,
            # with the element index from `chain` and a byte index appended. The
            # bytes after the literal are the zeroed terminator/padding, so no
            # terminator store is emitted.
            if elem_t.kind == "str":
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                    raise NotImplementedError(
                        f"LITHON-E0114: element {i} of `{name}` is a str, and a str is built "
                        f"from a string literal (6.2)")
                data = elt.value.encode("utf-8")
                if len(data) >= elem_t.cap:
                    raise NotImplementedError(
                        f"LITHON-E0111: element {i} of `{name}` is `{elem_t.render()}` and holds "
                        f"at most {elem_t.cap - 1} bytes plus a NUL terminator, but the literal "
                        f"is {len(data)} bytes (6.2)")
                # 4.2. A tuple's literal is checked for completeness by counting
                # its leaf stores, so each str element must contribute exactly its
                # capacity in stores. The content bytes are followed by explicit
                # zero bytes (the terminator and padding), which is the same value
                # the declaration already zeroed -- harmless, and it keeps the
                # tuple's store count equal to its flat-leaf count. A list is not
                # tracked that way, so it emits only the content bytes.
                n_stores = elem_t.cap if kind == "tuple" else len(data)
                for j in range(n_stores):
                    byte = data[j] if j < len(data) else 0
                    j_reg = self.new_reg()
                    self.emit(f"{j_reg} = const_i64 {j}")
                    b_reg = self.new_reg()
                    self.emit(f"{b_reg} = const_i64 {byte}")
                    self.emit(f"IndexStore {name}, {', '.join(chain + [j_reg])}, {b_reg}")
                continue
            if isinstance(elt, (ast.Tuple, ast.List, ast.Dict)):
                raise NotImplementedError(
                    f"LITHON-E0123: element {i} of `{name}` is a container literal, but its "
                    f"declared element is {elem_t.render()}; a container is not a value (4.1)")
            val_reg = self.build_expr(elt)
            self.emit(f"IndexStore {name}, {', '.join(chain)}, {val_reg}")

    def build_tuple_declaration(self, name, decl_t, suffix, value_node):
        """Emits a tuple declaration, with or without a tuple literal.

        4.2. The valueless store comes first and is not optional. It reserves
        and zeroes the run, and both execution tiers size the flat leaf run from
        it.

        The literal's elements then arrive as one IndexStore per LEAF, which is
        the same opcode an illegal t[0] = v would use. There is deliberately no
        second opcode for building a tuple: adding one would give the
        immutability rule two places to live, and one of them would be a runtime
        check. Instead the type checker recognises exactly this sequence -- a
        valueless declaration followed by its own N leaf stores -- and refuses
        every other IndexStore into a tuple.

        So the literal is the only way to get a non zero value into a tuple, and
        it is checked rather than trusted. Arity, element kinds and widths all go
        through the normal element rules.
        """
        self.emit(f"store {name}{suffix}")
        if value_node is None:
            return
        self._build_container_literal(name, decl_t, value_node, [])

    def build_list_declaration(self, name, decl_t, suffix, value_node):
        """Emits a list declaration, with or without a list literal.

        4.1. The valueless store comes first, exactly as for a tuple: it
        reserves and zeroes the run, and both tiers size the flat leaf run from
        it. A bare `xs: list[int[64], 6]` is the all-zero list and is legal.

        A list literal then fills the run with one IndexStore per leaf. Unlike a
        tuple, a list is mutable, so the same opcode is the ordinary `xs[k] = v`
        write; there is no second constructor opcode to keep in sync.

        The literal must name AT MOST `capacity` elements. `len(xs)` is the
        live length -- the number of elements the literal names, or the full
        capacity for a bare declaration -- carried in the companion `<name>.__len`
        slot (Phase 6.7). A shorter literal therefore leaves room for
        `list.append`; the unused slots stay zeroed and are not counted.
        """
        self.emit(f"store {name}{suffix}")
        # 6.7. `parts: list[str[K], M] = s.split(sep)`: the pieces are produced at
        # run time, so the length companion is written from the split's result
        # rather than from a literal count.
        if value_node is not None and self._is_split_call(value_node):
            self._build_str_split(name, decl_t, value_node)
            return
        self._declare_list_length(name, decl_t.cap, value_node)
        if value_node is None:
            return
        self._build_container_literal(name, decl_t, value_node, [])

    @staticmethod
    def _is_split_call(node):
        """True for a `something.split(...)` call used as a list initializer.

        `_build_str_split` then decides whether the receiver is a bounded-string
        place and reports it if not. A struct method that happens to be called
        `split` cannot be the initializer of a list -- Lithon has no list value
        for a method to return -- so routing every `.split(...)` here is safe and
        gives a better message than "needs a list literal".
        """
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "split")

    def _build_str_split(self, dest, decl_t, call):
        """`parts: list[str[K], M] = s.split(sep)` (6.7).

        The one way to produce a list of strings: a run-time split of a bounded
        string into a DECLARED, fixed-capacity list of bounded strings. There is
        no list value to hand back, so the destination is named on the left and
        the split writes into it. One op (`strsplit`) scans the source, writes
        each piece NUL-terminated (truncated to K-1 bytes if it is too long) and
        returns the piece count; the frontend stores that count into the
        destination's length companion, so len()/print()/a reduction see exactly
        the pieces produced. More pieces than M pieces is the runtime trap.
        """
        if len(call.args) != 1 or call.keywords:
            raise NotImplementedError(
                "LITHON-E0114: str.split() takes exactly one separator, e.g. "
                's.split(",") -- the whitespace-splitting form is not supported (6.7)')
        if decl_t.kind != "list" or decl_t.elem is None or decl_t.elem.kind != "str":
            raise NotImplementedError(
                "LITHON-E0114: str.split() writes a list of bounded strings, so "
                "the destination must be declared list[str[K], M] -- got "
                f"{decl_t.render()} (6.7)")
        src = self._str_place_name(call.func.value)
        if src is None:
            raise NotImplementedError(
                "LITHON-E0114: str.split() must be called on a bounded-string "
                "place -- a str variable, a str struct field, or valueof() of a "
                "str pointer. A str function parameter is not yet a string place "
                "(6.7)")
        sub = call.args[0]
        sep = self._str_place_name(sub)
        if sep is None:
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value == "":
                    raise NotImplementedError(
                        "LITHON-E0114: str.split() needs a non-empty separator -- "
                        'Python raises on "" too (6.7)')
                sep, _cap = self._materialize_str_literal(sub.value)
            else:
                raise NotImplementedError(
                    "LITHON-E0114: str.split() needs a string literal or a str "
                    "variable as its separator (6.7)")
        r = self.new_reg()
        self.emit(f"{r} = strsplit {src}, {sep}, {dest}{render_type_suffix(decl_t)}")
        self.emit(f"store {self._list_len_name(dest)}, {r} : int[64]")
        # The piece count is a run-time value: no reduction or membership scan
        # over `dest` can be unrolled, and `dest` is not a compile-time length.
        self.list_len[dest] = None

    def _declare_list_length(self, name, cap, value_node):
        """Declares and initialises the live-length companion of list `name`.

        The companion is an ordinary `int[64]` variable named `<name>.__len`.
        Like any scalar declaration, its type rides on its first (only)
        assignment rather than on a separate valueless store -- the typechecker
        binds a name from an annotated store, and a bare `store x : int[64]`
        would leave the name declared but unassigned. Its initial value is the
        literal's element count (a bare declaration counts every slot as live),
        and it is what `len`, `print` and a reduction read back.
        """
        len_name = self._list_len_name(name)
        if value_node is None:
            count = cap
        elif isinstance(value_node, (ast.List, ast.Tuple)):
            count = len(value_node.elts)
        else:
            # A non-literal initialiser is refused by _build_container_literal;
            # count the run as fully live here so the companion is still defined
            # by the time that error is raised.
            count = cap
        init = self.new_reg()
        self.emit(f"{init} = const_i64 {count}")
        self.emit(f"store {len_name}, {init} : int[64]")
        self.list_len[name] = count

    def build_str_declaration(self, name, decl_t, suffix, value_node):
        """Emits a `str[N]` declaration, with or without a string literal.

        6.2. Like a list, the valueless typed store comes first: it reserves and
        ZEROES the N bytes, so a bare `s: str[8]` is the empty string and both
        tiers size the run from it. A string literal then arrives as one byte
        IndexStore per UTF-8 byte -- the same op an ordinary `s[i] = v` uses, so
        there is no separate constructor opcode to keep in sync. The bytes after
        the literal stay zero, which is exactly the NUL terminator: a literal of
        length L < N needs no explicit terminator store.
        """
        self.emit(f"store {name}{suffix}")
        if value_node is None:
            return
        if not (isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)):
            raise NotImplementedError(
                f"LITHON-E0114: a str is built from a string literal, so `{name}: ... = <expr>` "
                f"needs \"...\", not a single expression (6.2)")
        data = value_node.value.encode("utf-8")
        n = decl_t.cap
        if len(data) >= n:
            raise NotImplementedError(
                f"LITHON-E0111: `{name}: str[{n}]` holds at most {n - 1} bytes plus a NUL "
                f"terminator, but the literal is {len(data)} bytes (6.2)")
        for i, byte in enumerate(data):
            idx = self.new_reg()
            self.emit(f"{idx} = const_i64 {i}")
            val = self.new_reg()
            self.emit(f"{val} = const_i64 {byte}")
            self.emit(f"IndexStore {name}, {idx}, {val}")

    # ---- 6.2. Bounded strings as struct fields -----------------------------
    #
    # A `str[N]` field is N bytes of bounded-string storage inside a struct.
    # The struct's own blob keeps the N reserved bytes (so every other field's
    # offset is unchanged and `addressof(s)` still names a real run), but the
    # string's bytes live in a COMPANION byte run declared under the dotted name
    # `s.f` (or `s.inner.f`). That single choice lets every string operation --
    # byte read/write (Index/IndexStore), len (strlen), print (print_container)
    # and equality (streq) -- reuse the one code path a top-level `s: str[N]`
    # already uses, in both tiers, with no new IR opcode. The companion run is
    # kept in lock step with the blob's lifetime because it is declared right
    # after it, so a struct declared inside a loop resets its strings too.

    def _struct_str_slots(self, layout, prefix):
        """Yields (dotted-name, cap) for every str field reachable from `layout`,
        recursing through nested struct fields in declaration order."""
        for fname, field in layout.fields.items():
            path = prefix + "." + fname
            if field["kind"] == "str":
                yield path, field["cap"]
            elif field["kind"] == "struct":
                yield from self._struct_str_slots(
                    self.structs[field["struct_name"]], path)

    def _emit_struct_str_decls(self, var, layout):
        """Declares the companion byte run of every str field of `var`, right
        after the struct's own `store ... : struct[N]`. A struct with no str
        field emits nothing."""
        for path, cap in self._struct_str_slots(layout, var):
            self.emit(f"store {path} : str[{cap}]")

    def _assign_struct_str_field(self, synth, cap, value_node):
        """`s.f = "literal"`: the field's whole-string write.

        Exactly like a top-level `s: str[N] = "literal"`, one IndexStore per
        byte, followed by zeros through the capacity so a shorter literal both
        NUL-terminates and clears the tail of a longer previous value.
        """
        if not (isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)):
            raise NotImplementedError(
                f"LITHON-E0114: a str is built from a string literal, so `{synth} = <expr>` "
                f"needs \"...\", not a single expression (6.2)")
        data = value_node.value.encode("utf-8")
        if len(data) >= cap:
            raise NotImplementedError(
                f"LITHON-E0111: `{synth}` holds at most {cap - 1} bytes plus a NUL "
                f"terminator, but the literal is {len(data)} bytes (6.2)")
        for i in range(cap):
            byte = data[i] if i < len(data) else 0
            idx = self.new_reg()
            self.emit(f"{idx} = const_i64 {i}")
            val = self.new_reg()
            self.emit(f"{val} = const_i64 {byte}")
            self.emit(f"IndexStore {synth}, {idx}, {val}")

    def _str_place_name(self, node):
        """The companion/declared VARIABLE name of a bounded-string place, or
        None when `node` does not name one.

        A place is a plain str variable (`s`), an attribute chain ending at a
        str field (`s.f`, `s.inner.f`), or `valueof(_p)` for a pointer whose
        pointee is a str and which names a str variable (`_p = addressof(s)`).
        It is used by `==`, by len(), by a print argument and by the index
        chain, all of which name the string rather than loading a value. This is
        a pure resolution: it never emits, which is what lets it be called for
        both operands of a comparison in source order.
        """
        if isinstance(node, ast.Name):
            decl = self.declared.get(node.id)
            if decl is not None and decl.kind == "str":
                return node.id
            return None
        if isinstance(node, ast.Attribute):
            try:
                _var, _off, kind, _w, synth = self._resolve_struct_field(node)
            except NotImplementedError:
                return None
            if kind == "str":
                return synth
            return None
        # 6.2. `valueof(_p)` where `_p` was minted from `addressof(s)` names the
        # very variable `s`. A pointer whose target is not a known str variable
        # (a parameter, or one walked by arithmetic) has no companion name, so
        # the call is not a place and is left to the valueof refusal.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "valueof" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                return self.ptr_str_names.get(arg.id)
        return None

    def _dict_key_operand(self, dict_name, key_expr):
        """The key operand a dict READ emits: `%reg` for an int/bool key, or the
        run's NAME for a str key.

        A bounded string has no scalar form, so a str key is named rather than
        held in a register. The dict's declared key type decides which shape is
        right, and a key that cannot become that shape is refused here rather
        than sent to the checker as a mismatched register.
        """
        decl = self.declared.get(dict_name)
        key_t = decl.key if decl is not None and decl.kind == "dict" else None
        if key_t is None or key_t.kind != "str":
            return self.build_expr(key_expr)
        place = self._str_place_name(key_expr)
        if place is None:
            if isinstance(key_expr, ast.Constant) and isinstance(key_expr.value, str):
                place, _cap = self._materialize_str_literal(key_expr.value)
            else:
                raise NotImplementedError(
                    f"LITHON-E0114: `{dict_name}` is a str-keyed dict, so its key must be a "
                    f"str variable or a string literal, e.g. d[k] or d[\"a\"] (4.3)")
        return place

    def build_dict_declaration(self, name, decl_t, suffix, value_node):
        """Emits a dict declaration, with or without a dict literal.

        4.3. The valueless store comes first, for the same reason a tuple has
        one. It is what reserves the table, and it is what zeroes the occupied
        array, so a bare declaration is an empty dict rather than a dict full of
        zero valued entries.

        Each entry then arrives as a DictStore carrying the key and the value.
        There is deliberately no bucket operand: the bucket is derived from the
        key by the one shared hash both tiers are written from, so they cannot
        disagree about where an entry lives. Emitting the bucket here instead
        would mean two independent hash implementations, one per tier.

        The literal may hold fewer entries than there are buckets, because N is
        the bucket count and not the entry count. That is the normal case for a
        hash table and it is what makes probing reachable at all.

        The key is either a scalar register (an int or bool key) or -- for a
        str key -- the NAME of the string run, because a bounded string has no
        scalar form to put in a register. The two shapes are told apart by the
        dict's declared key type, which is why the declaration travels with the
        name. 6.9. The VALUE has the same split for the same reason: a list or
        tuple value is one flat leaf run, so it too is built into a run and named
        rather than loaded into a register.
        """
        capacity = decl_t.cap
        key_t = decl_t.key
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
        str_key = key_t.kind == "str"
        # 6.9. A dict's value type may itself be a container (4.3). Such a value
        # has no scalar form to carry in a register, so it is built into a run
        # and the run is named in the store -- exactly the split a str key uses
        # between `args` and `name2`, one level over.
        container_value = decl_t.elem.kind in ("list", "tuple")
        # 6.8. A repeated string key is refused here, where the literals are, for
        # the same reason the checker refuses a repeated int key: two entries
        # cannot occupy one bucket, and the defined answer for a hand-written
        # table (overwrite) is not the answer a source program should get. The
        # checker cannot do it for a str key, because the run a literal
        # materialises into is SHARED between literals of the same capacity, so
        # the run name is not the key's identity -- two different two-byte texts
        # would look like one duplicate and two identical ones would look like an
        # overwrite.
        seen_str_keys = set()
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
            # 6.9. A value that is a container (a list or a tuple) is built into
            # its own run and the RUN is named in the store; a scalar value is a
            # register. The two are told apart by the declared value type, which
            # is why the declaration travels with the name -- the same
            # name-vs-register split a str key already uses in `name2`.
            if container_value:
                val_spec = self._materialize_container_value(decl_t.elem, val_node, i)
            else:
                val_spec = self.build_expr(val_node)
            if str_key:
                # 4.3. A str key. The literal is materialised into its own bounded
                # run and the run is NAMED in the store, so no register ever holds
                # the key -- the comparison at run time is byte-wise by content.
                if not isinstance(key_node.value, str):
                    raise NotImplementedError(
                        f"LITHON-E0123: dict key {i} is not a string, but this dict is "
                        f"declared with a str key type (4.3)")
                if key_node.value in seen_str_keys:
                    raise NotImplementedError(
                        f"LITHON-E0123: duplicate dict key {key_node.value!r} in the "
                        f"literal for '{name}' (4.3)")
                seen_str_keys.add(key_node.value)
                key_name, key_cap = self._materialize_str_literal(key_node.value)
                # 6.8. The literal is copied into a key_cap-wide slot, so it has
                # to FIT the declared key type. A literal of L bytes needs a run
                # of L+1 (the content plus its NUL), so the rule is L < N -- the
                # same bound that decides whether the literal could have been
                # assigned to a str[N] in the first place.
                if key_cap > key_t.cap:
                    raise NotImplementedError(
                        f"LITHON-E0123: dict key {i} is {len(key_node.value)} byte(s) long, "
                        f"so it does not fit this dict's key type str[{key_t.cap}] (4.3)")
                self.emit(f"DictStore {name}, {key_name}, {val_spec}")
                continue
            if isinstance(key_node.value, str):
                raise NotImplementedError(
                    f"LITHON-E0123: dict key {i} is a string, but this dict is declared "
                    f"with a {key_t.kind} key type; declare it dict[str[...], ...] (4.3)")
            if isinstance(key_node.value, float):
                raise NotImplementedError(
                    f"LITHON-E0123: dict key {i} is a float; 4.3 keys are int, bool or "
                    f"str only")
            key_reg = self.new_reg()
            # A bool key is emitted as a bool constant, not as int(key). The type
            # of the key register is what the checker compares against the
            # declared key type, so `dict[bool, ...] = {True: 1}` lowered to
            # const_i64 would reject its own literal.
            if isinstance(key_node.value, bool):
                self.emit(f"{key_reg} = const_bool {1 if key_node.value else 0}")
            else:
                self.emit(f"{key_reg} = const_i64 {int(key_node.value)}")
            self.emit(f"DictStore {name}, {key_reg}, {val_spec}")

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
        return render_type_suffix(decl)

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
                decl_key = Type("struct", width=layout.size, ref=node.annotation.id)
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
                # 6.2. Declare the companion byte run of each str field (if any),
                # so its storage exists and resets wherever the struct does.
                self._emit_struct_str_decls(name, layout)
                return
            # 4.7. An enum declaration looks like a struct one, but it may carry
            # a construction as its initializer: `e: E = E.A2(5)`. The blob is
            # reserved first (the same valueless store a struct uses), then the
            # constructor writes the tag and payload into it.
            if isinstance(node.annotation, ast.Name) and node.annotation.id in self.enums:
                layout = self.enums[node.annotation.id]
                decl_key = Type("enum", width=layout.size, ref=node.annotation.id)
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
            decl_t = parse_type_annotation(node.annotation, self.structs)
            kind, width = decl_t.kind, decl_t.cap
            suffix = render_type_suffix(decl_t)
            # 0.6.10. A name declares its type once per function. Writing a
            # DIFFERENT type on a second declaration of the same name has no
            # single answer for what the name holds, so it is refused here,
            # where both declarations are visible in source order. Same type
            # re-declaration is fine (it is how a name is re-opened, e.g. a
            # tuple restart).
            decl_key = decl_t
            prev_decl = self.declared.get(name)
            if prev_decl is not None and prev_decl != decl_key:
                def spell(d):
                    return render_type_suffix(d).lstrip(" :") or "?"
                raise NotImplementedError(
                    f"LITHON-E0122: `{name}` is re-declared with a different type: first "
                    f"declared {spell(prev_decl)}, now {spell(decl_key)} -- "
                    f"a name has one type (V1_SPEC 0.6.10)")
            self.declared[name] = decl_key
            if kind == "str":
                self.build_str_declaration(name, decl_t, suffix, node.value)
                return
            if kind == "tuple":
                self.build_tuple_declaration(name, decl_t, suffix, node.value)
                return
            if kind == "list":
                self.build_list_declaration(name, decl_t, suffix, node.value)
                return
            if kind == "dict":
                self.dict_names.add(name)
                self.build_dict_declaration(name, decl_t, suffix, node.value)
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
                # 6.2. The str pointee carries its capacity in `cap`, not
                # `width` (a str has no scalar width), so the stride used by
                # pointer arithmetic is the capacity -- `_p + 1` walks a whole
                # string. Every other pointee keeps its scalar width.
                if decl_t.elem.kind == "str":
                    self.var_types[name] = ("str", decl_t.elem.cap)
                else:
                    self.var_types[name] = (decl_t.elem.kind, decl_t.elem.width)
                if decl_t.elem.kind == "struct" and decl_t.elem.ref:
                    self.ptr_struct_names[name] = decl_t.elem.ref
                if node.value is None:
                    # 0.6.10. A bare `_p: ptr[T]` is a legal declaration: it
                    # names the type and emits nothing else, so _p starts
                    # UNASSIGNED -- not null. There is still no null pointer
                    # in Lithon: the first `_p = addressof(...)` is what
                    # assigns it, and a read before that is the same
                    # "not definitely assigned" error as for any scalar.
                    return
                value_reg = self.build_expr(node.value)
                # 6.2. A pointer that is born from `addressof(s)` (s a str[N])
                # names that run, so record the companion name alongside the
                # value. Any other initializer (a load, arithmetic) has no
                # companion until the plain-assignment path re-records one.
                if decl_t.elem.kind == "str" and value_reg in self.ptr_str:
                    self.ptr_str_names[name] = self.ptr_str[value_reg]
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
                # `xs[i][j]... = v`. Op::IndexStore carries the container name in
                # `name`, every index, and the value last. The chain is collected
                # the same way the read path does (and reversed to source order),
                # so `xs[i][j] = v` becomes one multi-index store.
                slices = []
                cur = target
                while isinstance(cur, ast.Subscript):
                    slices.append(cur.slice)
                    cur = cur.value
                if not isinstance(cur, ast.Name):
                    # 6.2. `s.f[i] = byte` (a str field's companion run) and
                    # `valueof(_p)[i] = byte` (the str a pointer names) take the
                    # same one-level IndexStore a top-level str variable does.
                    place = self._str_place_name(cur)
                    if place is not None and len(slices) == 1:
                        slices.reverse()
                        idx = self.build_expr(slices[0])
                        val = self.build_expr(node.value)
                        self.emit(f"IndexStore {place}, {idx}, {val}")
                        return
                    raise NotImplementedError(
                        "LITHON-E0117: only a plain variable, a str field or a str pointer "
                        "can be index-assigned one level deep, e.g. xs[i] = v, s.f[i] = v "
                        "or valueof(_p)[i] = v")
                slices.reverse()
                # 4.3. A dict subscript read is DictIndex, but there is no dict
                # subscript WRITE, so `d[k] = v` would quietly become an
                # IndexStore into a dict and be reported as a missing index
                # operation rather than as the immutability violation it is.
                if cur.id in self.dict_names:
                    raise NotImplementedError(
                        f"LITHON-E0117: cannot store into '{cur.id}': a dict is "
                        f"immutable (4.3); it is filled by its own literal, and "
                        f"after that the only operations are a read and contains")
                # 4.1. There is no container value to copy yet, so a
                # container-valued right-hand side is refused by name rather than
                # silently emitted as a one-cell store.
                if isinstance(node.value, ast.Name) and node.value.id in self.dict_names:
                    raise NotImplementedError(
                        f"LITHON-E0117: cannot store a container value into '{cur.id}[...]'; "
                        f"a container is not a value, so there is nothing to copy (4.1)")
                idxs = [self.build_expr(s) for s in slices]
                val = self.build_expr(node.value)
                self.emit(f"IndexStore {cur.id}, {', '.join(idxs)}, {val}")
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
                pf = self._resolve_ptr_field(target)
                if pf is not None:
                    reg, offset, kind, width, _ = pf
                    val_reg = self.build_expr(node.value)
                    self.emit(f"fieldstore {reg}, {offset}, {val_reg}"
                              f"{render_type_suffix(kind, width)}")
                    return
                var, offset, kind, width, synth = self._resolve_struct_field(target)
                if kind == "str":
                    # 6.2. `s.f = "literal"`: a whole-string write, like a
                    # top-level `s: str[N] = "literal"`. It is stored as one
                    # IndexStore per byte into the field's companion byte run,
                    # and every remaining byte through the capacity is zeroed so
                    # a shorter literal clears a longer previous one.
                    self._assign_struct_str_field(synth, width, node.value)
                    return
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
            # 6.2. A pointer assignment re-binds which string (if any) the
            # pointer names, mirroring what the load/store did to the register:
            # `_q = _p` keeps the companion, `_p = addressof(s)` sets it, and
            # any other value (`_p + k`) leaves the pointer without one.
            if name in self.ptr_names:
                self.ptr_str_names.pop(name, None)
                if value_reg in self.ptr_str:
                    self.ptr_str_names[name] = self.ptr_str[value_reg]
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
                # 6.1. Python's print takes any number of arguments, separated
                # by a space, one trailing newline. No string is ever built:
                # each argument is printed by a runtime call, the separator by
                # another, and the newline by a third. Three small builtins
                # rather than one variadic one keeps every tier's per-value
                # kind/formatter decision exactly where it already was.
                if not call.args:
                    self.emit("call print_nl")
                    return
                for i, arg in enumerate(call.args):
                    if i:
                        self.emit("call print_sep")
                    self._emit_print_arg(arg)
                self.emit("call print_nl")
                return
            if isinstance(call.func, ast.Attribute):
                # Phase 6.7. `xs.append(v)` as a statement.
                if call.func.attr == "append" and self._build_list_append(call):
                    return
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

        if isinstance(node, ast.Try):
            self.build_try(node)
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
                    param_t = parse_type_annotation(arg.annotation)
                    if param_t.kind == "ptr":
                        # 4.4. A pointer parameter cannot survive the function
                        # header: the header records a kind and a width but no
                        # pointee, so a passed pointer would degrade into a bare
                        # address that nothing could valueof. Refused at source
                        # rather than by the checker's header review.
                        raise NotImplementedError(
                            f"LITHON-E0124: parameter '{arg.arg}' cannot be a pointer: the "
                            f"function header records no pointee, so pointers "
                            f"are not passed by value (4.4)")
                    suffix = render_type_suffix(param_t).replace(" : ", ":")
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
                ret_t = parse_type_annotation(stmt.returns)
                if ret_t.kind == "ptr":
                    raise NotImplementedError(
                        f"LITHON-E0124: function '{stmt.name}' cannot return a pointer: like "
                        f"parameters, the header records no pointee (4.4)")
                # " : " is stripped because the return suffix uses " -> " and the
                # scalar path below writes the brackets itself.
                return_suffix = render_type_suffix(ret_t).replace(" : ", " -> ")

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
        print("usage: frontend.py <source.lio>", file=sys.stderr)
        return 1

    # A Lithon source file is `.lio`, and only `.lio`. The language is its own
    # thing now -- the extension is what says "this is a Lithon program" rather
    # than a Python script that happens to be shaped like one, and the frontend
    # is the one place every tier is entered through, so the check lives here
    # and not in each driver.
    if not sys.argv[1].endswith(".lio"):
        print(f"LITHON-E0114: Lithon source files use the .lio extension, "
              f"not '{sys.argv[1]}'", file=sys.stderr)
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
