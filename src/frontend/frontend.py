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
                f"dict annotation is missing its key or value type")
        k = render_type_suffix(key_kind, key_width).lstrip(" :")
        v = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : dict[{k}, {v}, {width}]"
    if kind == "ptr":
        # 4.4. `ptr[int[64]]`: the pointee is nested one level, exactly like a
        # container's element, but no capacity follows it. The C++ side reads
        # the pointee back through the same recursion, so it stays in the same
        # element fields a container's element would use.
        if elem_kind is None:
            raise NotImplementedError("ptr annotation is missing its pointee type")
        inner = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : ptr[{inner}]"
    if kind in CONTAINER_KINDS:
        if elem_kind is None:
            raise NotImplementedError(f"{kind} annotation is missing its element type")
        inner = render_type_suffix(elem_kind, elem_width).lstrip(" :")
        return f" : {kind}[{inner}, {width}]"
    if width is None or width == -1:
        return f" : {kind}"
    return f" : {kind}[{width}]"


def parse_type_annotation(node):
    """Returns (kind, width, elem_kind, elem_width, key_kind, key_width) from an
    annotation AST node, or raises NotImplementedError for forms this frontend
    slice doesn't handle.
    elem_kind/elem_width are None for every scalar and set for every container.
    key_kind/key_width are set only for a dict.
    Mirrors the C++ parse_type_string, kept independent since this is a separate
    scaffolding tool (frontend vs IR parser)."""
    if isinstance(node, ast.Name):
        if node.id == "bool":
            return "bool", -1, None, None, None, None
        if node.id in ("int", "float"):
            opts = "8, 16, 32 or 64" if node.id == "int" else "64"
            raise NotImplementedError(
                f"LITHON-E0105: {node.id} requires an explicit width -- "
                f"{node.id} must be {opts}")
        raise NotImplementedError(f"type '{node.id}' requires an explicit size, e.g. {node.id}[64]")

    if isinstance(node, ast.Subscript):
        if not isinstance(node.value, ast.Name):
            raise NotImplementedError("unsupported type annotation form")
        base = node.value.id
        if base == "dict":
            return _parse_dict_annotation(node.slice)
        if base == "ptr":
            return _parse_ptr_annotation(node.slice)
        if base in CONTAINER_KINDS:
            return _parse_container_annotation(base, node.slice)
        if base not in ("int", "float", "str"):
            raise NotImplementedError(f"unknown type '{base}'")
        size_node = node.slice
        if not isinstance(size_node, ast.Constant) or not isinstance(size_node.value, int):
            raise NotImplementedError(f"{base}[N] requires a literal integer size")
        _require_supported_width(base, size_node.value)
        return base, size_node.value, None, None, None, None

    raise NotImplementedError("unsupported type annotation form")


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
            "dict[K, V, N] needs a key type, a value type and a literal bucket "
            "count, e.g. dict[int[64], int[64], 8]")
    key_node, val_node, cap_node = slice_node.elts
    if not isinstance(cap_node, ast.Constant) or not isinstance(cap_node.value, int) \
            or isinstance(cap_node.value, bool):
        raise NotImplementedError("dict bucket count must be a literal integer")
    if cap_node.value <= 0:
        raise NotImplementedError(
            f"dict bucket count must be positive, got {cap_node.value}")
    if cap_node.value & (cap_node.value - 1):
        raise NotImplementedError(
            f"dict bucket count must be a power of two, got {cap_node.value}")
    key_kind, key_width, key_elem_kind, _, _, _ = parse_type_annotation(key_node)
    if key_elem_kind is not None:
        raise NotImplementedError("a dict key cannot itself be a container")
    val_kind, val_width, val_elem_kind, _, _, _ = parse_type_annotation(val_node)
    if val_elem_kind is not None:
        raise NotImplementedError("a dict value cannot itself be a container")
    return "dict", cap_node.value, val_kind, val_width, key_kind, key_width


def _parse_ptr_annotation(slice_node):
    """Parses the `T` of `ptr[T]`, e.g. `ptr[int[64]]`.

    4.4. The pointee is a plain scalar, and it must carry its size. An address
    is a number that denotes one cell of one type; pointing at a container
    would mean the address knows the container's shape, which the pointer
    cannot carry (its type data is just a kind and a width on the IR side, and
    a stack slot address on the run side). So the pointee grants the pointer
    exactly one of int, float or bool at a stated width -- the width is what
    valueof needs to know how many bytes to read.
    """
    if not isinstance(slice_node, (ast.Name, ast.Subscript)):
        raise NotImplementedError("ptr[T] needs a scalar pointee, e.g. ptr[int[64]] (4.4)")
    elem_kind, elem_width, elem_elem_kind, elem_elem_width, _, _ = \
        parse_type_annotation(slice_node)
    if elem_elem_kind is not None:
        raise NotImplementedError(
            f"a ptr pointee is a scalar, not a container (4.4); "
            f"ptr[{elem_kind}[...]] does not point at anything single")
    if elem_kind not in ("int", "float", "bool"):
        raise NotImplementedError(
            f"ptr pointees are int, float or bool only, got {elem_kind} (4.4)")
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
            f"{base}[T, N] needs an element type and a literal capacity, "
            f"e.g. {base}[int[64], 4]")
    elem_node, cap_node = slice_node.elts
    if not isinstance(cap_node, ast.Constant) or not isinstance(cap_node.value, int) \
            or isinstance(cap_node.value, bool):
        raise NotImplementedError(f"{base}[T, N] requires a literal integer capacity")
    if cap_node.value <= 0:
        raise NotImplementedError(f"{base} capacity must be positive, got {cap_node.value}")
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


def scalar_cell_bytes(kind, width):
    """Bytes a scalar cell occupies in a frame: int[N] and float[N] are N/8
    bytes, bool is one. This is the stride a pointer to such a cell adds and
    the length a valueof of it reads."""
    if kind == "bool":
        return 1
    if kind in ("int", "float"):
        if width is None or width <= 0:
            raise NotImplementedError(
                f"{kind} needs a stated width to be pointed at, e.g. {kind}[64] (4.4)")
        return width // 8
    raise NotImplementedError(
        f"only a scalar cell can be pointed at, got {kind} (4.4)")


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
    def __init__(self, fn_rename=None):
        self.fn_rename = fn_rename or {}
        self.reg_counter = 0
        self.block_counter = 0
        self.ifexpr_counter = 0
        self.blocks = []
        self.current = None
        # 4.3. Names declared as a dict, so a subscript on one lowers to a hash
        # and a probe instead of an array address. Recorded here rather than
        # looked up, because the frontend does not otherwise track a variable's
        # type and a wrong guess would pick the wrong opcode silently.
        self.dict_names = set()
        # 4.4. Names declared as a pointer, and -- for the two things the
        # frontend must know about a pointer's pointee without consulting a
        # type table -- the scalar type of every annotated scalar variable and
        # the pointee carried by every register that holds a pointer.
        #
        # var_types: name -> (kind, width) for annotated scalar variables. It is
        # what addressof needs to know how big a cell it points at, and tracing
        # through _p requires the same cell size at every pointer arithmetic.
        #
        # ptr_types: register -> (kind, width) of the pointee of the pointer
        # that register holds. A pointer value only enters the world through
        # addressof(x) (whose pointee is var_types[x]) and load of a pointer
        # name (whose pointee is the declaration), and add/sub keep their
        # operand's pointee, so this stays complete by construction.
        self.var_types = {}
        self.ptr_types = {}
        self.ptr_names = set()
        # 0.6.10. Every `name: T` written in this function, recorded as the raw
        # parse_type_annotation tuple. An annotation-only declaration emits no
        # store, so this is where the declaration lives: it is what lets a
        # later plain `name = ...` carry the declared type into the IR (the
        # checker range-checks the first assignment against it, 0.6.5) and
        # what makes a second `name: OtherType` a re-declaration error. Since
        # each function gets a fresh IRBuilder, this is per-function.
        self.declared = {}

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
        return b

    def emit(self, line):
        self.current.lines.append(f"    {line}")

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
                raise NotImplementedError(f"operator {op_type.__name__} not supported yet")
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
                        "a pointer offset must be a literal integer, e.g. `_p + 1` (4.4)")
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
                raise NotImplementedError("chained comparisons (a < b < c) not supported yet")
            left = self.build_expr(node.left)
            right = self.build_expr(node.comparators[0])
            op_map = {ast.Lt: "lt", ast.Gt: "gt", ast.Eq: "eq"}
            op_type = type(node.ops[0])
            if op_type not in op_map:
                raise NotImplementedError(f"comparison {op_type.__name__} not supported yet")
            r = self.new_reg()
            self.emit(f"{r} = {op_map[op_type]} {left}, {right}")
            return r

        if isinstance(node, ast.BoolOp):
            if len(node.values) != 2:
                raise NotImplementedError("and/or with exactly two operands supported for now")
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
            if not isinstance(node.func, ast.Name):
                raise NotImplementedError("only direct name calls are supported")
            if node.func.id == "print":
                raise NotImplementedError("print() is a statement in this slice, not an expression")
            if node.func.id == "len":
                # Op::Len names a container variable and returns the capacity.
                # It is not a call: there is no `len` function to emit a `call`
                # for, and a user function named `len` would otherwise collide.
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError("len() takes exactly one variable")
                r = self.new_reg()
                self.emit(f"{r} = Len {node.args[0].id}")
                return r
            if node.func.id == "contains":
                # 4.3. Like len, this is not a call to a `contains` function. It
                # is a builtin that lowers to one opcode, so a user function of
                # that name would collide and is rejected by the checker instead.
                if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
                    raise NotImplementedError(
                        "contains() takes a dict variable and a key, e.g. contains(d, k)")
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
                        "valueof() takes exactly one pointer, e.g. valueof(_p) (4.4)")
                arg_reg = self.build_expr(node.args[0])
                if arg_reg not in self.ptr_types:
                    raise NotImplementedError(
                        "valueof() needs a pointer, e.g. valueof(_p) (4.4)")
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
                        "addressof() takes exactly one plain variable, e.g. "
                        "addressof(x) (4.4)")
                name = node.args[0].id
                if name not in self.var_types:
                    raise NotImplementedError(
                        f"addressof('{name}') needs the size of '{name}', so "
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
                        f"{node.func.id}() takes exactly two int[64] operands, "
                        f"e.g. {node.func.id}(a, b) (E0303)")
                op = {"wrap_add": "wrapadd", "wrap_sub": "wrapsub",
                      "wrap_mul": "wrapmul"}[node.func.id]
                left = self.build_expr(node.args[0])
                right = self.build_expr(node.args[1])
                if left in self.ptr_types or right in self.ptr_types:
                    raise NotImplementedError(
                        f"{node.func.id}() is integer-only -- pointer arithmetic "
                        f"is not wrapping arithmetic (E0303, int[64] operands only)")
                r = self.new_reg()
                self.emit(f"{r} = {op} {left}, {right}")
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
                    "only a plain variable can be indexed, e.g. xs[i]; there is no "
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

        raise NotImplementedError(f"expression node {type(node).__name__} not supported yet")

    def build_if(self, node):
        cond_reg = self.build_expr(node.test)
        branch_block = self.current

        then_label = self.reserve_label()
        else_label = self.reserve_label() if node.orelse else None
        merge_label = self.reserve_label()

        target_else = else_label if else_label else merge_label
        branch_block.lines.append(f"    branch {cond_reg}, {then_label}, {target_else}")

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
            raise NotImplementedError("while/else is not supported (the else clause would be silently dropped)")
        header_label = self.reserve_label()
        body_label = self.reserve_label()
        exit_label = self.reserve_label()

        self.emit(f"jump {header_label}")

        self.start_block(header_label)
        cond_reg = self.build_expr(node.test)
        self.emit(f"branch {cond_reg}, {body_label}, {exit_label}")

        self.start_block(body_label)
        for stmt in node.body:
            self.build_stmt(stmt)
        self.emit(f"jump {header_label}")

        self.start_block(exit_label)

    def build_for(self, node):
        if node.orelse:
            raise NotImplementedError("for/else is not supported (the else clause would be silently dropped)")
        if not isinstance(node.target, ast.Name):
            raise NotImplementedError("only a single name for-target is supported")
        if not (isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == "range"
                and len(node.iter.args) == 1):
            raise NotImplementedError("only for x in range(N) is supported in this slice")

        loop_var = node.target.id
        limit_reg = self.build_expr(node.iter.args[0])

        zero_reg = self.new_reg()
        self.emit(f"{zero_reg} = const_i64 0")
        # A range() counter is an int by construction: it is compared against
        # the limit and post-incremented, never anything else. The annotation
        # has to be emitted HERE rather than asked of the source, because the
        # source has no way to annotate a for-target -- `for k: int[64] in
        # range(n)` is not valid Python. With the type checker now
        # unconditional, leaving this store unannotated made every `for` loop
        # in the language a type error.
        self.emit(f"store {loop_var}, {zero_reg} : int[64]")

        header_label = self.reserve_label()
        body_label = self.reserve_label()
        exit_label = self.reserve_label()

        self.emit(f"jump {header_label}")

        self.start_block(header_label)
        i_reg = self.new_reg()
        self.emit(f"{i_reg} = load {loop_var}")
        cond_reg = self.new_reg()
        self.emit(f"{cond_reg} = lt {i_reg}, {limit_reg}")
        self.emit(f"branch {cond_reg}, {body_label}, {exit_label}")

        self.start_block(body_label)
        for stmt in node.body:
            self.build_stmt(stmt)
        i_reg2 = self.new_reg()
        self.emit(f"{i_reg2} = load {loop_var}")
        one_reg = self.new_reg()
        self.emit(f"{one_reg} = const_i64 1")
        inc_reg = self.new_reg()
        self.emit(f"{inc_reg} = add {i_reg2}, {one_reg}")
        self.emit(f"store {loop_var}, {inc_reg}")
        self.emit(f"jump {header_label}")

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
                f"a tuple is built from a tuple literal, so `{name}: ... = <expr>` "
                f"needs (a, b, ...), not a single expression (4.2)")
        if len(value_node.elts) != capacity:
            raise NotImplementedError(
                f"`{name}: tuple[..., {capacity}]` needs exactly {capacity} element"
                f"{'' if capacity == 1 else 's'}, got {len(value_node.elts)}")
        for i, elt in enumerate(value_node.elts):
            # A nested container literal would need the outer stride to be the
            # inner's byte size, which is the same non-power-of-two SIB limit
            # that rejects nested containers as a type.
            if isinstance(elt, (ast.Tuple, ast.List)):
                raise NotImplementedError(
                    f"tuple element {i} is a container literal; 4.2 tuples hold "
                    f"int, float or bool elements only")
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                raise NotImplementedError(
                    f"tuple element {i} is a string literal; 4.2 tuples hold "
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
                f"a dict is built from a dict literal, so `{name}: ... = <expr>` "
                f"needs {{k: v, ...}}, not a single expression (4.3)")
        if len(value_node.keys) > capacity:
            raise NotImplementedError(
                f"`{name}: dict[..., {capacity}]` has {capacity} bucket"
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
                    f"dict key {i} is not a literal; a dict key must be a "
                    f"constant, because the bucket is decided from it (4.3)")
            if isinstance(key_node.value, str):
                raise NotImplementedError(
                    f"dict key {i} is a string; 4.3 keys are int or bool only")
            if isinstance(key_node.value, float):
                raise NotImplementedError(
                    f"dict key {i} is a float; 4.3 keys are int or bool only")
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
                raise NotImplementedError("only simple name targets are supported for annotations")
            name = node.target.id
            kind, width, elem_kind, elem_width, key_kind, key_width = \
                parse_type_annotation(node.annotation)
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
                    f"`{name}` is re-declared with a different type: first "
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
                        f"a pointer is named with a leading underscore, write "
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
                        f"a name that starts with '_' must be a pointer, but "
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
                raise NotImplementedError("only single-target assignment is supported")
            target = node.targets[0]
            if isinstance(target, ast.Subscript):
                # `xs[i] = v`. Op::IndexStore carries the container name in `name`
                # and only the index and value as ValueIds.
                if not isinstance(target.value, ast.Name):
                    raise NotImplementedError(
                        "only a plain variable can be index-assigned, e.g. xs[i] = v")
                # 4.3. A dict subscript read is DictIndex, but there is no dict
                # subscript WRITE, so `d[k] = v` would quietly become an
                # IndexStore into a dict and be reported as a missing index
                # operation rather than as the immutability violation it is.
                if target.value.id in self.dict_names:
                    raise NotImplementedError(
                        f"cannot store into '{target.value.id}': a dict is "
                        f"immutable (4.3); it is filled by its own literal, and "
                        f"after that the only operations are a read and contains")
                idx = self.build_expr(target.slice)
                val = self.build_expr(node.value)
                self.emit(f"IndexStore {target.value.id}, {idx}, {val}")
                return
            if not isinstance(target, ast.Name):
                raise NotImplementedError("only single-name assignment targets are supported")
            if isinstance(node.value, ast.Tuple):
                # 4.2. A tuple literal belongs to its annotated declaration, where
                # the element type and the capacity are known. Naming the form
                # beats letting it fall through to the generic "expression node
                # Tuple not supported yet", which is true and useless.
                raise NotImplementedError(
                    f"a tuple is built on its annotation, so write "
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
                raise NotImplementedError("only simple name targets are supported for augmented assignment")
            op_map = BITWISE_OPS if type(node.op) in BITWISE_OPS else BINARY_OPS
            op_type = type(node.op)
            if op_type not in op_map:
                raise NotImplementedError(f"augmented operator {op_type.__name__} not supported yet")
            name = node.target.id
            current = self.new_reg()
            self.emit(f"{current} = load {name}")
            if name in self.ptr_names:
                # 4.4. `_p += k` is pointer arithmetic, so the RHS is a literal
                # element count scaled to bytes, same as `_p + k` in expressions
                # (see the BinOp case). The step lands on the tagged pointer;
                # the register below is the pointer's new value.
                if op_type not in (ast.Add, ast.Sub):
                    raise NotImplementedError(
                        "a pointer only walks add and sub, got an augmented " +
                        f"operator for `{name}` (4.4)")
                rhs_lit = self.literal_int(node.value)
                if rhs_lit is None:
                    raise NotImplementedError(
                        f"a pointer offset must be a literal integer, e.g. "
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
                    raise NotImplementedError("print() with exactly one argument is supported")
                arg_reg = self.build_expr(call.args[0])
                self.emit(f"call print, {arg_reg}")
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

        if isinstance(node, ast.Return):
            if node.value is not None:
                val_reg = self.build_expr(node.value)
                self.emit(f"return {val_reg}")
            else:
                self.emit("return")
            return

        raise NotImplementedError(f"statement node {type(node).__name__} not supported yet")

    def render(self, header_line):
        out = [header_line]
        for b in self.blocks:
            out.append(f"{b.label}:")
            out.extend(b.lines)
        return "\n".join(out)


def build_program(tree):
    module_parts = []

    user_fn_names = {s.name for s in tree.body if isinstance(s, ast.FunctionDef)}
    fn_rename = {}
    if ENTRY_POINT in user_fn_names:
        mangled = MANGLED_PREFIX + ENTRY_POINT
        while mangled in user_fn_names:
            mangled = MANGLED_PREFIX + mangled
        fn_rename[ENTRY_POINT] = mangled

    main_builder = IRBuilder(fn_rename)
    main_builder.start_block(main_builder.reserve_label())

    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            fb = IRBuilder(fn_rename)
            fb.start_block(fb.reserve_label())

            param_strs = []
            for arg in stmt.args.args:
                if arg.annotation is not None:
                    kind, width, elem_kind, elem_width, key_kind, key_width = \
                        parse_type_annotation(arg.annotation)
                    if kind == "ptr":
                        # 4.4. A pointer parameter cannot survive the function
                        # header: the header records a kind and a width but no
                        # pointee, so a passed pointer would degrade into a bare
                        # address that nothing could valueof. Refused at source
                        # rather than by the checker's header review.
                        raise NotImplementedError(
                            f"parameter '{arg.arg}' cannot be a pointer: the "
                            f"function header records no pointee, so pointers "
                            f"are not passed by value (4.4)")
                    suffix = render_type_suffix(kind, width, elem_kind, elem_width,
                                                key_kind, key_width).replace(" : ", ":")
                    param_strs.append(f"{arg.arg}{suffix}")
                else:
                    param_strs.append(arg.arg)

            return_suffix = ""
            if stmt.returns is not None:
                kind, width, elem_kind, elem_width, key_kind, key_width = \
                    parse_type_annotation(stmt.returns)
                if kind == "ptr":
                    raise NotImplementedError(
                        f"function '{stmt.name}' cannot return a pointer: like "
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

    tree = ast.parse(source)
    ir_text = build_program(tree)
    sys.stdout.write(ir_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
