#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <memory>

// Basic-block IR with PHI nodes. Do not add instructions beyond
// M1's required set without updating docs/V1_SPEC.md and
// docs/ROADMAP.md first.

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
    //   Index      args = {index}                 result = element
    //   IndexStore args = {index, value}          no result
    //   Len        args = {}                      result = capacity
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
    DictStore,
    DictIndex,
    DictContains,

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
    // narrow zero-extend, and the value's kind feeds both the typechecker's
    // compare rules and the print guard.
    AddressOf,
    ValueOf
};

using ValueId = uint32_t;
constexpr ValueId kInvalidValue = 0xFFFFFFFF;

struct Instr {
    Op op;
    ValueId result;
    std::vector<ValueId> args;

    int64_t int_imm = 0;
    double float_imm = 0.0;
    std::string name;
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
