#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <memory>

// Basic-block IR with PHI nodes. Do not add instructions beyond the current
// required set without documenting them first (README.md and the web docs under
// web/apps/web/src/docs/).

namespace lithon::ir {

enum class Op : uint8_t {
    ConstInt,
    ConstFloat,
    ConstBool,
    Load,
    Store,

    Add,
    Sub,
    Mul,
    Div,

    // Wrapping add/sub/mul (E0303 opt-out). Default Add/Sub/Mul trap on
    // int64 overflow so a wrapped 64-bit result cannot pass silently; these
    // are the deliberate escape hatch, compiled to the same machine code with
    // the overflow trap elided and executed by the interpreter with wrapping
    // arithmetic. int[64] operands only (v1; see docs/lithon_error_system.md
    // section 3a).
    WrapAdd,
    WrapSub,
    WrapMul,

    // Integer/float remainder. Typed like Mul (int iff both operands are
    // int, else float), NOT like Div: true division has to widen because a
    // quotient generally is not an integer, but a remainder never leaves the
    // domain. Semantics are C's, i.e. the sign follows the dividend and the
    // result truncates toward zero -- NOT Python's floored `%`, where
    // -7 % 3 is 2. See interpreter.cpp's apply_binop for why.
    Mod,

    Lt,
    Gt,
    Eq,
    // The negation of Eq, first class rather than an Eq plus a Not: the
    // optimizer, branch fusion and the print guard all then see the real
    // predicate. It matters most for floats, where it is NOT the mirror of
    // Eq's machine test -- ucomisd is unordered on NaN, so `!=` is
    // `setne || setp` while `==` is `sete && setnp`.
    Ne,

    And,
    Or,
    Not,

    // Bitwise, and integer-only. These are deliberately separate from the
    // And/Or above rather than overloading them: those are logical ops with
    // value semantics (the result IS one of the operands, so `x and y` is y
    // whenever x is truthy), which is not a bit operation at all. A single
    // op that meant both would make `&` and `and` differ in result TYPE, not
    // just in result value, and the print guard's int/bool tracking -- which
    // is the whole reason these are separate -- would have nothing to key on.
    //
    // Shl/Shr are 64-bit two's-complement. Shr is an ARITHMETIC shift: it
    // replicates the sign bit, matching Python's >> and the interpreter's
    // int64_t >>, so -1 >> 1 is -1 here too. Shift counts are range-checked
    // to 0..63 at compile time when literal, and at runtime otherwise,
    // because x86 masks the count to 6 bits -- a count of 64 would silently
    // execute as 0 and quietly produce the wrong answer instead of trapping.
    Shl,
    Shr,
    BitAnd,
    BitOr,
    BitXor,

    // A direct call. `name` is either a user function (args = its arguments)
    // or one of the built-in runtime calls. The print family is a set of
    // builtins, not a variadic one, so every tier's per-value format decision
    // stays in one place: `print` (one value + "\n"), `print_bare` (one value,
    // no newline), `print_sep` (" ") and `print_nl` ("\n"). The frontend lowers
    // Python's `print(a, b, c)` to print_bare(print_sep|print_bare)* print_nl.
    // `syscall` is the other builtin (see its opcode below).
    Call,
    Return,

    Branch,
    Jump,

    Phi,

    // 4.1. Container access. All three name the container VARIABLE in
    // `name` rather than taking a register, because the address is not the
    // value: a list lives in a run of frame slots and only the variable knows
    // where that run starts. `args` therefore holds the operands.
    //
    //   Index      args = {i0, i1, ...}           result = leaf
    //   IndexStore args = {i0, i1, ..., value}    no result
    //   Len        args = {}                      result = capacity
    //
    // A nested access `xs[i][j]` is ONE instruction carrying the whole index
    // chain, not a chain of single-index ops: an inner container has no value to
    // be an intermediate, and the flat-leaf offset is a single sum over the
    // levels (see ContainerLayout). A one-level access `xs[i]` is the chain of
    // length one, so every existing producer and reader is unchanged.
    //
    // Len takes no operand and is not a runtime load: N is part of the type,
    // so `len(xs)` is a compile-time constant and is folded to one.
    Index,
    IndexStore,
    Len,

    // 4.3. Dict access. Same convention as the container ops above: the
    // variable is named in `name`, the operands are in `args`.
    //
    //   DictStore     args = {key, value}    no result
    //   DictIndex     args = {key}           result = value
    //   DictContains  args = {key}           result = bool
    //
    // DictStore fills a slot of the table. The key is a compile-time constant
    // because a dict is built only from a literal, which is what lets the bucket
    // be resolved statically instead of at run time.
    //
    // DictIndex and DictContains take the key as a run-time value, so they emit
    // a real hash and a real probe. DictContains never traps and reports a
    // missing key as False. DictIndex traps on a missing key.
    //
    // 4.3. A str key is a run of bytes, not a value, so it cannot be an operand
    // register. It is named in `name2` instead -- the key kind is part of the
    // dict's declared type, so a reader that has the layout knows which of the
    // two shapes it is holding. An int/bool key leaves `name2` empty and uses
    // `args` exactly as above; a str key names its run in `name2` and shifts the
    // remaining operands down:
    //
    //   DictStore     name2 = key, args = {value}
    //   DictIndex     name2 = key, result = value
    //   DictContains  name2 = key, result = bool
    //
    // 6.9. A dict's VALUE may itself be a container (a list or a tuple), and a
    // container is a run of bytes with no scalar form, so it too is named rather
    // than loaded -- the same split, one level over. A container value is named
    // in `name3` (a str key and a container value can co-occur, which is exactly
    // `dict[str[N], list[T, M], B]`). DictStore is the only op that can carry one
    // (a dict is built by its literal and never assigned), so it is the only one
    // that reads `name3`:
    //
    //   DictStore     name2 = str key (or ""), name3 = container value (or ""),
    //                 args  = {value} for a named value, {} for a named key only,
    //                 {key, value} for neither.
    DictStore,
    DictIndex,
    DictContains,

    // 6.1. Printing a whole container. A container is a run of cells, not a
    // value, so it cannot be an operand of print()/print_bare() -- the print
    // family's closed set is scalars only. This op names the container VARIABLE
    // (like Index/Len/DictStore), and each tier walks the cells and renders the
    // Python-repr form of the aggregate: `[e0, e1, ...]` for a list,
    // `(e0, e1, ...)` for a tuple (with the trailing comma a one-element tuple
    // needs), and `{k0: v0, k1: v1, ...}` for a dict in bucket order. It writes
    // no trailing newline, so the frontend still emits print_nl after it.
    //
    // The container's shape is carried in the trailing " : T" suffix exactly as
    // a declaration carries it, so the op is self-describing and both engines
    // agree on the element kind from the same fields.
    PrintContainer,

    // 4.4. Pointers. A pointer is an address, not a value to print, so these
    // two ops together with the ptr[T] type are the whole surface exposed to
    // the language.
    //
    //   AddressOf  args = {}                      result = address (ptr[T])
    //   ValueOf    args = {ptr}                   result = pointee
    //
    // AddressOf names the target VARIABLE in `name` (like the container ops):
    // the address of a scalar slot is derived from the variable, not from a
    // ValueId. The result's pointee type comes from the builtin's ellipsis.
    //
    // ValueOf takes a pointer ValueId in args[0] and loads the pointee through
    // it. The RESULT is the pointee, so the trailing " : T" instruction suffix
    // names the pointee type directly (e.g. `%v = valueof %p : int[64]`): the
    // compiler needs to know whether the load is an 8-byte move, a movsd, or a
    // narrow movsx/movsxd (a Lithon int is signed, so a narrow int load
    // sign-extends), and the value's kind feeds both the typechecker's
    // compare rules and the print guard.
    AddressOf,
    ValueOf,

    // 4.6. Struct field access. A struct variable is a byte blob whose layout
    // the FRONTEND decides, so the runtime never needs the field table: each
    // field op carries the field's byte offset directly and the field type in
    // the trailing " : T" suffix. Both tiers read/write raw little-endian
    // bytes at `base + offset`, truncating a store to the field width and
    // sign-extending an int load (zero-extending a bool) -- the exact
    // semantics of a packed container lane.
    //
    //   FieldLoad   args = {}   result = field value     name = struct var
    //   FieldStore  args = {value}   no result           name = struct var
    //
    // The byte offset is a compile-time layout artifact, so it is carried in
    // `int_imm` rather than as a ValueId the way a list index is. Field access
    // is only ever to a SCALAR run: a struct has no value, so a struct field
    // is reachable only as the base of a deeper `a.b.c` chain, which the
    // frontend resolves to a single leaf field op. `valueof` of a struct is
    // refused.
    FieldLoad,
    FieldStore,

    // Hardening item 5. Explicit numeric conversions and terminal input.
    //
    //   ToFloat   args = {value}   result = float[64]
    //   ToInt     args = {value}   result = int[type_width]
    //   InputInt  args = {}        result = int[type_width]
    //
    // ToFloat widens an int[8|16|32] EXACTLY to float[64] (the value is
    // sign-extended in its slot, so a single cvtsi2sd is exact) or passes a
    // float[64] through unchanged. int[64] -> float[64] is refused at compile
    // time as lossy (E0106): a 64-bit integer has up to 64 significant bits
    // and a double only 53, so the conversion would silently round.
    //
    // ToInt truncates a float[64] toward zero into int[type_width] and traps
    // (E0309) on a NaN, an infinity, or a magnitude outside the target width.
    // This is the ONLY float -> int direction that exists: there is no
    // int -> int narrowing (see the conversion matrix in V1_SPEC 0.6.11).
    //
    // InputInt reads one line from stdin and parses it as int[type_width].
    // `name` carries the prompt, HEX-ENCODED so an arbitrary prompt (spaces,
    // commas, brackets, newlines) survives the comma-split text IR; the empty
    // string means no prompt. It traps E0306 (not an integer), E0307 (out of
    // the target width) and E0308 (end of input). The `int(w, input(prompt))`
    // fusion in the frontend is the only way these are spelled.
    ToFloat,
    ToInt,
    InputInt,
    // Range guard: dynamic step == 0 traps with E0310.
    GuardNonZero,

    // Phase III. The raw syscall primitive. A compiler-recognized builtin
    // `syscall(number, a1..a6)` lowers to exactly this op:
    //
    //   Syscall   args = {number, a1, a2, a3, a4, a5, a6}   result = int[64]
    //
    // (the trailing arguments are absent, not zero, when the call supplies
    // fewer of them). The result is the kernel's raw return, UNCHECKED --
    // on Linux a failure is -errno, exactly as the kernel reports it. There
    // is no `syscall` function to call and no fallible wrapper in this slice;
    // a program that wants to inspect the failure compares the result itself.
    //
    // Arguments are int[64] or pointers. A pointer argument is a real host
    // address in BOTH tiers (4.4), so a kernel syscall taking a pointer
    // (`bind`/`connect` take `&sockaddr_in`, `send`/`recv` take a buffer) reads
    // and writes exactly the bytes the language does; only the address value
    // differs across tiers, and only the named bytes are observed. The
    // typechecker accepts int and ptr and refuses a float or an aggregate --
    // neither is passed by value.
    //
    // The instruction is deliberately platform-neutral: it names the raw
    // operation, and the platform fork (the x86-64 `syscall` instruction on
    // Linux, a host-serviceable hook on Windows, whose raw ABI is not stable)
    // lives in one place, jit/syscall_runtime.h. Both engines reach it through
    // that same helper, so the interpreter and the JIT cannot disagree about
    // the calling convention.
    Syscall,

    // 6.2. Bounded strings. `str[N]` is N bytes of NUL-terminated storage -- the
    // same packed byte run a `list[int[8], N]` has, but with string semantics:
    // its length is the number of bytes before the first 0x00 (capped at N), and
    // printing renders those bytes as text rather than as a bracketed byte list.
    // It shares the container storage/index machinery -- the declaration is a
    // valueless typed `store`, a byte read is `Index s, i` (int[8]) and a byte
    // write is `IndexStore s, i, v` -- so only the three string-specific
    // operations below need new opcodes.
    //
    //   StrLen    name = string var                            result = int[64]
    //   StrEq     name = string var, name2 = other string var  result = bool
    //   StrFind   name = string var, name2 = needle var        result = int[64]
    //   StrCount  name = string var, name2 = needle var        result = int[64]
    //
    // StrEq compares the two runs byte for byte over the full N (both are
    // NUL-terminated and zero-padded, so whole-run equality is content
    // equality). A whole string is never a register value, so printing it goes
    // through PrintContainer with a `str[N]` suffix rather than print().
    //
    // 6.4. s.find(sub) and s.count(sub) are the two substring scans. Both name
    // TWO string runs, exactly as StrEq does, because a whole string is never a
    // register value: the haystack is `name` and the needle is `name2`. The
    // capacities need not match. find returns the lowest index or -1 (in band,
    // as in Python); count returns the number of NON-overlapping matches. The
    // empty needle is the Python edge: find -> 0, count -> len(haystack) + 1.
    // 6.7. s.split(sep) writes the pieces of `name` into the list `name3`, whose
    // shape (`list[str[K], M]`) rides the trailing type suffix. It is a bare
    // statement (no result): a split produces a number of strings that is only
    // known at run time, and a whole string is never a register value, so the
    // destination must be a declared, fixed-capacity list of bounded strings.
    // The op sets that list's live-length companion. `name2` is the separator.
    StrLen,
    StrEq,
    // The negation of StrEq, first class for the same reason scalar Ne is:
    // `s != t` is their length-or-byte difference, not "not streq" obscured by
    // a following Not for the optimizer to unpick.
    StrNe,
    StrFind,
    StrCount,
    StrSplit,

    // 6.10. try/except over the built-in Tier-3 traps. These three ops are
    // control-flow *markers*, not work: they delimit a protected region and its
    // handlers so both tiers can route a trap to the matching handler instead of
    // terminating. There is no exception object and no unwind -- the handlers
    // are ordinary blocks, and a trap is a jump.
    //
    //   TryEnter  name  = comma-separated exception kinds this try catches
    //             name2 = their handler labels, in the same order
    //             Marks the entry to the protected region. Pushes a region.
    //   TryLeave  Pops the innermost region. Marks normal completion of the
    //             protected region (the `else` runs after this point).
    //   TryCatch  name = the exception kind this handler block catches, or
    //             "Exception" for the catch-all. Placed as the FIRST instruction
    //             of each handler block, so a trap inside a handler is not
    //             re-caught by the same try.
    TryEnter,
    TryLeave,
    TryCatch
};

// 6.10. The closed set of catchable Tier-3 traps, keyed by the message the
// throwing site already uses. Both tiers call this, so they agree on which
// exception a trap is: a program must not catch on one tier and die on the
// other. Anything else -- a stack overflow, an internal invariant, a segfault --
// classifies as "" and is NOT catchable, which is what stops `except Exception`
// from swallowing a crash.
inline std::string classify_trap_message(const std::string& what) {
    if (what.find("division by zero") != std::string::npos) return "ZeroDivisionError";
    if (what.find("modulo by zero") != std::string::npos) return "ZeroDivisionError";
    if (what.find("list index out of range") != std::string::npos) return "IndexError";
    if (what.find("LITHON-E0303") != std::string::npos) return "OverflowError";
    if (what.find("LITHON-E0307") != std::string::npos) return "OverflowError";
    if (what.find("LITHON-E0309") != std::string::npos) return "ValueError";
    if (what.find("shift count out of range") != std::string::npos) return "ValueError";
    if (what.find("LITHON-E0310") != std::string::npos) return "ValueError";
    if (what.find("LITHON-E0306") != std::string::npos) return "ValueError";
    if (what.find("LITHON-E0308") != std::string::npos) return "EOFError";
    if (what.find("dict key not found") != std::string::npos) return "KeyError";
    if (what.find("too many pieces for the list") != std::string::npos) return "ValueError";
    return "";
}

using ValueId = uint32_t;
constexpr ValueId kInvalidValue = 0xFFFFFFFF;

// 6.10. Splits the `+`-separated payload of a TryEnter/TryCatch marker (the
// frontend writes "" for an empty one). Shared by the interpreter's and the
// JIT's handler-region tracking so the two tiers parse the marker identically.
// `+` and not `,`: the IR text parser splits a line's arguments on commas, so a
// comma-separated list inside `name` would be torn into extra arguments.
inline std::vector<std::string> split_marks(const std::string& s) {
    std::vector<std::string> out;
    if (s.empty()) return out;
    size_t start = 0;
    while (true) {
        size_t sep = s.find('+', start);
        if (sep == std::string::npos) {
            out.push_back(s.substr(start));
            break;
        }
        out.push_back(s.substr(start, sep - start));
        start = sep + 1;
    }
    return out;
}

// A resolved type, recursive so a container element or a dict key/value can
// itself be a container. Mirrors the frontend `Type` field for field. The flat
// per-Instr strings below are the legacy one-level projection; new readers
// recurse through `elem`/`key` instead.
struct Type {
    std::string kind;             // int | float | str | bool | struct | ptr | list | tuple | dict
    int width = -1;               // scalar width, or struct byte size
    int cap = -1;                 // list/tuple capacity, or dict bucket count
    std::shared_ptr<Type> elem;   // element (list/tuple), value (dict), pointee (ptr)
    std::shared_ptr<Type> key;    // dict key
    std::string ref;              // struct/enum name, source-level only

    bool is_container() const {
        return kind == "list" || kind == "tuple" || kind == "dict";
    }
    bool is_scalar() const {
        return kind == "int" || kind == "float" || kind == "str" ||
               kind == "bool" || kind == "struct";
    }
};

// 4.1 (phase 3). Flat-leaf layout of a list/tuple type. Leaves are packed
// contiguously in row-major order (last index varies fastest), so the whole
// aggregate is one run of leaf cells with no per-level padding and no
// indirection. This is the representation BOTH tiers use -- the JIT as frame
// bytes, the interpreter as a flat vector of leaf values -- which is what keeps
// them byte-identical.
//
// A scalar leaf occupies its packed sizeof: int/float -> width/8, bool -> 1.
// Nesting only multiplies the cell count. `strides[k]` is the byte distance one
// step in index level k covers, and the deepest stride equals the leaf size, so
// the flat byte offset of `xs[i0][i1]...` is sum_k idx_k * strides[k].
//
// `valid` is false when the type is not a list/tuple, when a capacity is
// non-positive, or when the leaf has no packed representation (str, a nested
// dict, ...) -- callers must reject rather than lay anything out.
struct ContainerLayout {
    bool valid = false;
    int leaf_bytes = 0;              // sizeof(leaf)
    int leaf_count = 0;              // total leaves in the blob
    int total_bytes = 0;             // leaf_bytes * leaf_count
    std::vector<int> strides;        // one per index level; strides.back() == leaf_bytes
};

inline int scalar_leaf_bytes(const Type& t) {
    if (t.kind == "bool") return 1;
    if (t.kind == "int" || t.kind == "float")
        return (t.width > 0 && t.width % 8 == 0) ? t.width / 8 : 0;
    return 0;   // str, or anything else without packed storage
}

inline ContainerLayout container_layout(const Type& t) {
    ContainerLayout out;
    // 6.2. A str[N] has a flat-leaf layout (N byte leaves) even though it is not
    // a list/tuple/dict. It shares the walk below so Index/IndexStore and the
    // declaration zeroing can address it through one code path.
    if (!t.is_container() && t.kind != "str") return out;
    // Walk the list/tuple spine. A dict can only hold scalar values (4.3), so
    // it never appears as an inner level here.
    std::vector<const Type*> levels;
    const Type* cur = &t;
    // 6.2. A str[N] is a level too: it is N bytes whose element is the byte
    // int[8], so `list[str[8], 4]` flattens to 32 byte leaves with strides
    // [8, 1] exactly as `list[list[int[8], 8], 4]` would.
    while (cur && (cur->kind == "list" || cur->kind == "tuple" ||
                   cur->kind == "str")) {
        levels.push_back(cur);
        cur = cur->elem.get();
    }
    if (!cur || !cur->is_scalar()) return out;
    const int lb = scalar_leaf_bytes(*cur);
    if (lb <= 0) return out;
    int count = 1;
    for (const Type* lv : levels) {
        if (lv->cap <= 0) return out;
        count *= lv->cap;
    }
    out.leaf_bytes = lb;
    out.leaf_count = count;
    out.total_bytes = count * lb;
    out.strides.resize(levels.size());
    int stride = lb;
    for (int k = static_cast<int>(levels.size()) - 1; k >= 0; --k) {
        out.strides[static_cast<size_t>(k)] = stride;
        stride *= levels[k]->cap;
    }
    out.valid = true;
    return out;
}

struct Instr {
    Op op;
    ValueId result;
    std::vector<ValueId> args;

    int64_t int_imm = 0;
    double float_imm = 0.0;
    std::string name;
    // 6.2. A second variable name, for the one op that names two variables:
    // StrEq compares two string runs, so it cannot carry both in `name`.
    std::string name2;
    // 6.7. A third variable name, for the one op that names three: StrSplit
    // reads a source str (`name`), a separator str (`name2`) and writes a list
    // of bounded strings (`name3`). Every other op leaves it empty.
    std::string name3;
    std::string type_kind;   // "int" | "float" | "str" | "bool" | "list" | "" (none)
    int type_width = -1;      // bit width; for "list" this is the CAPACITY, not a width

    // 4.1. The element type of a container, so `list[int[64], 10]` survives the
    // round trip through the text format. `type_kind`/`type_width` alone can
    // only say "list of 10" -- not what is IN it -- and the whole point of a
    // typed container is that `list[int[64]]` and `list[float[64]]` are
    // different types that must not be interchanged.
    //
    // Kept flat rather than as a recursive node because every existing reader of
    // type_kind stays correct and untouched: a scalar simply leaves these empty,
    // which is exactly the state it was already in.
    std::string type_elem_kind;   // element kind for list/tuple/ptr; "" when not a container
    int type_elem_width = -1;     // element width; -1 when absent

    // 4.3. A dict has two element types, so the pair above is not enough. For
    // `dict[int[64], bool, 8]` the pair carries the VALUE and these carry the
    // KEY.
    //
    // Adding a second pair rather than a nested node keeps every existing
    // reader correct and untouched, which is the same argument that made the
    // first pair flat instead of a recursive node. A non dict container leaves
    // both empty, which is the state they were already in.
    std::string type_key_kind;    // key kind for dict; "" otherwise
    int type_key_width = -1;      // key width for dict; -1 otherwise

    // The same declaration as a recursive tree, so a nested container
    // (`list[tuple[int[64], 4], 2]`) survives with its inner shape. Null for an
    // instruction with no type suffix.
    std::shared_ptr<Type> decl_type;
};

struct BasicBlock {
    std::string label;
    std::vector<Instr> instrs;
};

struct Function {
    std::string name;
    std::vector<std::string> params;
    std::vector<std::string> param_type_kinds;   // parallel to params; "" if untyped
    std::vector<int> param_type_widths;           // parallel to params; -1 if untyped/bool
    std::string return_type_kind;                 // "" if untyped
    int return_type_width = -1;
    std::vector<BasicBlock> blocks;
};

struct Module {
    std::vector<Function> functions;
};

} // namespace lithon::ir
