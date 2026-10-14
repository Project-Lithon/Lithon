#include "interpreter.h"
#include "runtime/value.h"
#include "jit/float_runtime.h"
#include "jit/input_runtime.h"
#include "jit/print_guard.h"
#include "jit/stack_guard.h"
#include "jit/syscall_runtime.h"
#include "dict_hash.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <unordered_map>
#include <stdexcept>
#include <vector>

namespace lithon::interp {

using lithon::LithonValue;
using namespace lithon::ir;

namespace {

class Frame {
public:
    std::unordered_map<ValueId, LithonValue> regs;
    std::unordered_map<std::string, LithonValue> vars;
    // 4.1. Containers live apart from scalar variables: a list is a run of
    // values, not one, and the JIT likewise keeps it in a run of frame slots
    // rather than in a variable's single slot. Keyed by the container's name.
    std::unordered_map<std::string, std::vector<LithonValue>> lists;

    // 4.3. A dict is three parallel arrays rather than one, which is the same
    // shape the JIT lays out in the frame: keys, occupied markers, values.
    //
    // Occupancy is kept separately instead of being inferred from the key,
    // because a key is a value and every int value is a legal key. Zero is a
    // key like any other, so a key array alone cannot say whether a bucket
    // holds the key 0 or holds nothing at all.
    struct DictTable {
        std::vector<LithonValue> keys;
        std::vector<LithonValue> values;
        std::vector<bool> occupied;
        // 4.3. How a key is reduced to its canonical form before it is hashed or
        // compared. Both tiers read these two fields rather than the declaration,
        // so the two cannot disagree about what counts as the same key.
        bool bool_keys = false;
        int key_width = 64;
    };
    std::unordered_map<std::string, DictTable> dicts;

    // 4.6. A struct is a packed run of bytes, not a value and not a run of
    // values: it is storage whose layout the frontend computed. Keeping it as
    // raw bytes here (rather than as a map of field name -> value) is what makes
    // the interpreter agree with the JIT byte for byte -- a field write truncates
    // to the field's low bytes and a read sign-extends an int field (the same
    // packed-lane semantics as a list element of that width).
    std::unordered_map<std::string, std::vector<uint8_t>> structs;

    std::vector<uint8_t>& get_struct(const std::string& name, const char* what) {
        auto it = structs.find(name);
        if (it == structs.end()) {
            throw std::runtime_error(std::string("interpreter: ") + what + " '" + name +
                                     "' which is not a struct");
        }
        return it->second;
    }

    std::vector<LithonValue>& get_list(const std::string& name, const char* what) {
        auto it = lists.find(name);
        if (it == lists.end()) {
            throw std::runtime_error(std::string("interpreter: ") + what + " '" + name +
                                     "' which is not a container");
        }
        return it->second;
    }

    DictTable& get_dict(const std::string& name, const char* what) {
        auto it = dicts.find(name);
        if (it == dicts.end()) {
            throw std::runtime_error(std::string("interpreter: ") + what + " '" + name +
                                     "' which is not a dict");
        }
        return it->second;
    }

    LithonValue get_reg(ValueId id) const {
        auto it = regs.find(id);
        if (it == regs.end()) {
            throw std::runtime_error("interpreter: reference to undefined value id");
        }
        return it->second;
    }

    LithonValue get_var(const std::string& name) const {
        auto it = vars.find(name);
        if (it == vars.end()) {
            throw std::runtime_error("interpreter: reference to undefined variable '" + name + "'");
        }
        return it->second;
    }

    // 4.4. Real addresses. A ptr[T] is a genuine host address in BOTH tiers,
    // not a synthetic handle: a kernel syscall handed one (5.2's send/recv
    // buffers) must be able to read and write the bytes behind it. The
    // interpreter used to hand out 0x1000, 0x1008, ... and resolve valueof
    // through a reverse map, which made every pointer unusable by anything
    // outside the interpreter.
    //
    // Each address-taken variable gets its OWN 8-byte block rather than a
    // slice of one growing arena: std::vector reallocates when it grows, which
    // would move every address already handed out and leave a live pointer
    // dangling. unordered_map is node-based, so a mapped vector's buffer stays
    // put across later insertions -- which is the whole reason for this shape.
    //
    // The slot is 8 bytes because that is what the JIT gives every variable too,
    // so the two layouts agree. Narrow values occupy the low bytes; see
    // sync_arena below.
    std::unordered_map<std::string, std::vector<uint8_t>> arena_slots;
    std::unordered_map<int64_t, std::string> var_of_addr;

    // Writes `v` into the variable's block in the same little-endian order the
    // JIT's frame stores it, so the bytes a pointer names are the bytes the
    // native tier would have written. A float goes out as its IEEE-754 bits,
    // which is what store_float_var's movsd does natively.
    void sync_arena(const std::string& name, const LithonValue& v) {
        auto it = arena_slots.find(name);
        if (it == arena_slots.end()) return;   // not address-taken: fast value form
        std::vector<uint8_t>& slot = it->second;
        uint64_t bits = 0;
        if (v.is_float()) {
            const double d = v.as_float();
            std::memcpy(&bits, &d, sizeof bits);
        } else {
            bits = static_cast<uint64_t>(v.as_int());
        }
        for (size_t i = 0; i < slot.size() && i < 8; ++i)
            slot[i] = static_cast<uint8_t>((bits >> (8 * i)) & 0xFF);
    }

    int64_t address_of(const std::string& name) {
        // 4.4. A struct is already a byte run, so its base IS its address and
        // no slot is allocated: the pointer names the same bytes fieldload and
        // fieldstore read and write. This is what makes a pointer into struct
        // memory real for a kernel syscall (5.2).
        auto s = structs.find(name);
        if (s != structs.end()) {
            const int64_t addr =
                static_cast<int64_t>(reinterpret_cast<uintptr_t>(s->second.data()));
            var_of_addr[addr] = name;
            return addr;
        }
        auto it = arena_slots.find(name);
        if (it != arena_slots.end())
            return static_cast<int64_t>(reinterpret_cast<uintptr_t>(it->second.data()));
        if (vars.find(name) == vars.end()) {
            throw std::runtime_error("interpreter: reference to undefined variable '" + name + "'");
        }
        auto ins = arena_slots.emplace(name, std::vector<uint8_t>(8, 0));
        std::vector<uint8_t>& slot = ins.first->second;
        const int64_t addr =
            static_cast<int64_t>(reinterpret_cast<uintptr_t>(slot.data()));
        var_of_addr[addr] = name;
        // The variable may already hold a value -- `a = 5` before
        // `_p = addressof(a)` -- so the block is seeded from it rather than
        // left zero, which would make a pointer taken later read a stale zero.
        auto v = vars.find(name);
        if (v != vars.end()) sync_arena(name, v->second);
        return addr;
    }

    // Which struct blob a field op targets.
    //
    // 4.4, second half. A field op with no base argument names its struct, as
    // it always has. With one, the base is a POINTER's address: the address
    // resolves back through the same map address_of populated, so a pointer
    // into a struct reaches the very bytes fieldload/fieldstore read and
    // write -- there is no copy and no second copy of the blob to keep in
    // step. A store carries the value at args[0] and the base at args[1], so
    // the two never collide.
    std::string field_target(const Instr& in, size_t base_arg) const {
        if (in.args.size() > base_arg) {
            LithonValue p = get_reg(in.args[base_arg]);
            if (!p.is_int()) {
                throw std::runtime_error(
                    "interpreter: field access through a non-address base");
            }
            return name_behind(p.as_int());
        }
        return in.name;
    }

    // The name behind a pointer, or an error naming the failure.
    std::string name_behind(int64_t addr) const {
        auto back = var_of_addr.find(addr);
        if (back == var_of_addr.end()) {
            throw std::runtime_error(
                "interpreter: dereference of a pointer that names no live object -- "
                "dangling and out-of-bounds pointers are out of scope (4.4)");
        }
        return back->second;
    }
};


// The value a container element holds before anything is written to it. Zero
// of the element's own kind, so a freshly declared list[float[64],4] reads
// back 0.0 rather than an int zero, and reading an unwritten element is
// defined instead of whatever the frame happened to contain. The native tier
// has to produce the same zeros, or the two tiers disagree on this.
LithonValue zero_of_kind(const std::string& elem_kind) {
    if (elem_kind == "float") return LithonValue::make_float(0.0);
    if (elem_kind == "bool")  return LithonValue::make_bool(false);
    return LithonValue::make_int(0);
}

// A valueless `store xs : list[T,N]` is a declaration, not an assignment: it
// names the container and its capacity and carries no value to store.
bool is_container_declaration(const Instr& in) {
    return in.args.empty() && (in.type_kind == "list" || in.type_kind == "tuple");
}

bool is_dict_declaration(const Instr& in) {
    return in.args.empty() && in.type_kind == "dict";
}

// 4.6. A valueless `store s : struct[N]` is a struct declaration: it reserves N
// zero bytes. There is no field list in the IR -- the layout lives in the frontend
// and reaches the field ops as byte offsets -- so the interpreter needs only the
// total size, exactly like the allocator.
bool is_struct_declaration(const Instr& in) {
    return in.args.empty() && in.type_kind == "struct";
}

// 4.6. Little-endian byte access, matching the packed lanes the JIT writes. A
// load sign-extends an int field (zero-extends a bool) and a store truncates to
// the field's low bytes, so a struct field of width w behaves exactly like a
// list[int[w]] element. The interpreter works in bytes rather than in a
// field-name -> value map precisely so that this truncation is forced, not
// chosen.
int64_t struct_load_int(const std::vector<uint8_t>& bytes, int offset, int width,
                        bool is_bool) {
    if (offset < 0 || width <= 0 ||
        static_cast<size_t>(offset) + static_cast<size_t>(width) > bytes.size()) {
        throw std::runtime_error("interpreter: struct field read out of range");
    }
    uint64_t v = 0;
    for (int i = 0; i < width; ++i)
        v |= static_cast<uint64_t>(bytes[static_cast<size_t>(offset + i)]) << (8 * i);
    if (is_bool) return v != 0 ? 1 : 0;
    // A Lithon int[w] is SIGNED: a negative lane must read back negative, the
    // same result the JIT's movsx/movsxd produces. Signing here and
    // zero-extending there was a real tier disagreement -- list[int[16]] element
    // -5 printed -5 in the interpreter and 65531 natively.
    switch (width) {
        case 1: return static_cast<int8_t>(v);
        case 2: return static_cast<int16_t>(v);
        case 4: return static_cast<int32_t>(v);
        default: return static_cast<int64_t>(v);
    }
}

void struct_store_int(std::vector<uint8_t>& bytes, int offset, int width, int64_t value) {
    if (offset < 0 || width <= 0 ||
        static_cast<size_t>(offset) + static_cast<size_t>(width) > bytes.size()) {
        throw std::runtime_error("interpreter: struct field write out of range");
    }
    const uint64_t raw = static_cast<uint64_t>(value);
    for (int i = 0; i < width; ++i)
        bytes[static_cast<size_t>(offset + i)] = static_cast<uint8_t>((raw >> (8 * i)) & 0xFF);
}

// 4.3. Two keys are the same key when they are the same after normalization, so
// True and 1 collide by design. Comparing the raw stored values would be enough
// for int keys but would make {True: a, 1: b} two entries, and the probe would
// find whichever came first with no way to tell that the table is wrong.
int64_t canonical(const LithonValue& key, const Frame::DictTable& t) {
    return dict::canonical_key(key.as_int(), t.bool_keys, t.key_width);
}

bool same_key(const LithonValue& a, const LithonValue& b, const Frame::DictTable& t) {
    return canonical(a, t) == canonical(b, t);
}

// Walks forward from the key's own bucket until it finds the key or an empty
// bucket. An empty bucket ends the search because linear probing only ever
// fills forward from a start, so nothing past a hole can belong to this key.
//
// The step count is bounded by the bucket count even though the typechecker has
// already guaranteed there is a free bucket. That guarantee is what makes a
// miss terminate, and the bound is what makes it terminate even if the table
// were somehow full, which is the difference between reporting a missing key and
// reading off the end of the arrays.
int64_t probe_bucket(const Frame::DictTable& t, const LithonValue& key, bool* found) {
    const int64_t buckets = static_cast<int64_t>(t.occupied.size());
    const int64_t start = dict::bucket_of(canonical(key, t), static_cast<int>(buckets));
    int64_t b = start;
    for (int64_t step = 0; step < buckets; ++step) {
        if (!t.occupied[static_cast<size_t>(b)]) {
            *found = false;
            return b;
        }
        if (same_key(t.keys[static_cast<size_t>(b)], key, t)) {
            *found = true;
            return b;
        }
        b = (b + 1) & (buckets - 1);
    }
    *found = false;
    return start;
}

LithonValue apply_binop(Op op, LithonValue lhs, LithonValue rhs) {
    bool either_float = lhs.is_float() || rhs.is_float();

    if (!lhs.is_int() && !lhs.is_float()) {
        throw std::runtime_error("interpreter: binop on a non-numeric value");
    }
    if (!rhs.is_int() && !rhs.is_float()) {
        throw std::runtime_error("interpreter: binop on a non-numeric value");
    }

    if (op == Op::Div) {
        double a = lhs.is_float() ? lhs.as_float() : static_cast<double>(lhs.as_int());
        double b = rhs.is_float() ? rhs.as_float() : static_cast<double>(rhs.as_int());
        // Python raises ZeroDivisionError for any zero divisor, including
        // floats and -0.0, rather than producing inf/nan the way raw IEEE
        // hardware division does. The JIT's Div emits a comisd check for the
        // same condition, so the two engines agree here by construction --
        // which is what lets run_tier_diff.py treat the interpreter as an
        // oracle for float code at all.
        if (b == 0.0) throw std::runtime_error("division by zero");
        return LithonValue::make_float(a / b);
    }

    if (either_float) {
        double a = lhs.is_float() ? lhs.as_float() : static_cast<double>(lhs.as_int());
        double b = rhs.is_float() ? rhs.as_float() : static_cast<double>(rhs.as_int());
        switch (op) {
            case Op::Add: return LithonValue::make_float(a + b);
            case Op::Sub: return LithonValue::make_float(a - b);
            case Op::Mul: return LithonValue::make_float(a * b);
            case Op::Mod: {
                // Same zero-divisor rule as Div above, for the same reason: the
                // JIT checks it, so the two engines agree by construction. NaN
                // divisors propagate rather than trap, again matching Div.
                if (b == 0.0) throw std::runtime_error("modulo by zero");
                // fmod is exactly the truncated (sign-follows-dividend)
                // remainder, so the float path needs no sign correction.
                double r = std::fmod(a, b);
                // A zero result is normalized to +0.0. fmod keeps the dividend's
                // sign, so fmod(-0.0, 2.0) is -0.0, but the JIT's four-instruction
                // a - trunc(a/b)*b sequence cannot: IEEE defines x - x as +0.0,
                // so it necessarily loses the sign on an exact division. Rather
                // than spend three more instructions (shift the sign bit out, shift
                // it back, XOR) to reproduce a distinction print shows but
                // arithmetic never observes, both engines return +0.0, which is
                // also what Python does: -0.0 % 2.0 is 0.0.
                return LithonValue::make_float(r == 0.0 ? 0.0 : r);
            }
            default:
                throw std::runtime_error("interpreter: not a binary arithmetic op");
        }
    }

    int64_t a = lhs.as_int();
    int64_t b = rhs.as_int();
    switch (op) {
        // Default int64 add/sub/mul TRAP on overflow (LITHON-E0303) rather
        // than wrapping: a wrapped 64-bit answer is a silently wrong answer
        // (the factorial bug, find_e.py 25!), and a bounded word has no wider
        // type to widen to. The JIT emits the same check as a jo after the
        // arithmetic, and the same text, so the two tiers agree byte for byte
        // -- which is what run_tier_diff.py relies on. wrap_add/wrap_sub/
        // wrap_mul are the deliberate opt-out: they wrap, exactly as Add/Sub/
        // Mul did before this change, and the optimizer FOLDS constant wrap
        // ops for them.
        case Op::Add: {
            int64_t r;
            if (__builtin_add_overflow(a, b, &r))
                throw std::runtime_error(
                    "LITHON-E0303: integer overflow in add -- use wrap_add() to wrap instead");
            return LithonValue::make_int(r);
        }
        case Op::Sub: {
            int64_t r;
            if (__builtin_sub_overflow(a, b, &r))
                throw std::runtime_error(
                    "LITHON-E0303: integer overflow in sub -- use wrap_sub() to wrap instead");
            return LithonValue::make_int(r);
        }
        case Op::Mul: {
            int64_t r;
            if (__builtin_mul_overflow(a, b, &r))
                throw std::runtime_error(
                    "LITHON-E0303: integer overflow in mul -- use wrap_mul() to wrap instead");
            return LithonValue::make_int(r);
        }
        case Op::WrapAdd: return LithonValue::make_int(
            static_cast<int64_t>(static_cast<uint64_t>(a) + static_cast<uint64_t>(b)));
        case Op::WrapSub: return LithonValue::make_int(
            static_cast<int64_t>(static_cast<uint64_t>(a) - static_cast<uint64_t>(b)));
        case Op::WrapMul: return LithonValue::make_int(
            static_cast<int64_t>(static_cast<uint64_t>(a) * static_cast<uint64_t>(b)));
        case Op::Mod: {
            if (b == 0) throw std::runtime_error("modulo by zero");
            // int64_t has no representable result for INT64_MIN % -1: it is 2^63,
            // one past the maximum. C++ leaves that undefined and x86 `idiv`
            // raises #DE, which would take the whole process down instead of
            // producing a value. Wraparound arithmetic elsewhere in this
            // interpreter is implemented as C++ overflow, so the defined
            // answer here is the wrapped one: 0, which is also what the JIT
            // emits. Both engines must agree, and 0 is the only choice that
            // does not involve trapping.
            if (a == std::numeric_limits<int64_t>::min() && b == -1) return LithonValue::make_int(0);
            return LithonValue::make_int(a % b);
        }
        default:
            throw std::runtime_error("interpreter: not a binary arithmetic op");
    }
}

LithonValue apply_compare(Op op, LithonValue lhs, LithonValue rhs) {
    if ((!lhs.is_int() && !lhs.is_float()) || (!rhs.is_int() && !rhs.is_float())) {
        throw std::runtime_error("interpreter: comparison on a non-numeric value");
    }
    // Both sides int means an exact int64 comparison, matching the JIT's
    // integer cmp/setcc path. Converting to double first would round away
    // differences past 2^53 and disagree with native for large integers.
    if (lhs.is_int() && rhs.is_int()) {
        int64_t a = lhs.as_int(), b = rhs.as_int();
        switch (op) {
            case Op::Lt: return LithonValue::make_bool(a < b);
            case Op::Gt: return LithonValue::make_bool(a > b);
            case Op::Eq: return LithonValue::make_bool(a == b);
            default:
                throw std::runtime_error("interpreter: not a comparison op");
        }
    }
    double a = lhs.is_float() ? lhs.as_float() : static_cast<double>(lhs.as_int());
    double b = rhs.is_float() ? rhs.as_float() : static_cast<double>(rhs.as_int());
    switch (op) {
        case Op::Lt: return LithonValue::make_bool(a < b);
        case Op::Gt: return LithonValue::make_bool(a > b);
        case Op::Eq: return LithonValue::make_bool(a == b);
        default:
            throw std::runtime_error("interpreter: not a comparison op");
    }
}

LithonValue apply_boolop(Op op, LithonValue lhs, LithonValue rhs) {
    switch (op) {
        case Op::And: return lhs.is_truthy() ? rhs : lhs;
        case Op::Or:  return lhs.is_truthy() ? lhs : rhs;
        default:
            throw std::runtime_error("interpreter: not a bool op");
    }
}

// Bitwise/shift. Int-only, on purpose: there is no float bit pattern in
// Lithon, so `2.5 & 1` is a type error rather than a reinterpreting of the
// double's bits.
//
// The shift-count bound is the important part. x86 masks the count to its low
// 6 bits, so `x << 64` on hardware is `x << 0` -- silently the wrong answer,
// and the classic way shift bugs survive review. Python raises ValueError for
// a negative or oversized count, so trapping agrees with it; the typechecker
// rejects the literal case before we ever get here, and this is the backstop
// for the dynamic case.
LithonValue apply_bitop(Op op, LithonValue lhs, LithonValue rhs) {
    if (!lhs.is_int() || !rhs.is_int()) {
        throw std::runtime_error("interpreter: bitwise op on a non-int value "
                                 "(and/or/xor/shl/shr are integer-only)");
    }
    int64_t a = lhs.as_int(), b = rhs.as_int();
    switch (op) {
        case Op::BitAnd: return LithonValue::make_int(a & b);
        case Op::BitOr:  return LithonValue::make_int(a | b);
        case Op::BitXor: return LithonValue::make_int(a ^ b);
        case Op::Shl:
            if (b < 0 || b > 63) {
                // Fixed text, no count: run_tier_diff.py compares stderr
                // byte-for-byte and the JIT's trap passes a literal string, so
                // a formatted count here would show up as a tier diff. The
                // compile-time message for a literal count does include it.
                throw std::runtime_error("shift count out of range 0..63 for `<<`");
            }
            // Unsigned shift, then reinterpret: shifting a negative int64 left
            // into the sign bit is well-defined via the unsigned domain, while
            // the signed form is UB (C++17) / defined only as a modulo result
            // (C++20). Going through uint64_t makes it a plain bit operation
            // either way and matches the wrapping x86 shl performs.
            return LithonValue::make_int(
                static_cast<int64_t>(static_cast<uint64_t>(a) << static_cast<uint64_t>(b)));
        case Op::Shr:
            if (b < 0 || b > 63) {
                throw std::runtime_error("shift count out of range 0..63 for `>>`");
            }
            // Arithmetic (sign-propagating): C++20 mandates this for signed
            // operands, and it is what Python's >> does, so -1 >> 1 is -1.
            return LithonValue::make_int(a >> static_cast<uint64_t>(b));
        default:
            throw std::runtime_error("interpreter: not a bitwise op");
    }
}

// Per-function, per-value kinds from the SAME whole-module analysis the JIT
// uses to choose a print format (print_guard.h). A pointer is a plain int in
// this interpreter, so the value alone cannot say "print me in hex"; the static
// kind does, and sharing the analysis keeps both tiers agreeing on which prints
// are pointers. Set for the duration of run_main.
const std::vector<std::vector<lithon::jit::Kind>>* g_value_kinds = nullptr;

bool print_arg_is_ptr(const Module& module, const Function& fn, lithon::ir::ValueId id) {
    if (!g_value_kinds || module.functions.empty()) return false;
    if (&fn < module.functions.data() || &fn >= module.functions.data() + module.functions.size())
        return false;
    const size_t fi = static_cast<size_t>(&fn - module.functions.data());
    if (fi >= g_value_kinds->size()) return false;
    const auto& kinds = (*g_value_kinds)[fi];
    return id < kinds.size() && kinds[id] == lithon::jit::Kind::Ptr;
}

// 0x<lowercase hex>, no padding -- byte-for-byte what the JIT's "0x%llx\n"
// prints (compile_function.h kPtrPrintFormat).
void do_print_ptr(LithonValue v) {
    if (!v.is_int()) throw std::runtime_error("interpreter: print() of a pointer that is not an address");
    char buf[32];
    std::snprintf(buf, sizeof buf, "0x%llx", static_cast<unsigned long long>(v.as_int()));
    std::cout << buf << "\n";
}

void do_print(LithonValue v) {
    if (v.is_bool())        std::cout << (v.as_bool() ? "True" : "False") << "\n";
    else if (v.is_int())    std::cout << v.as_int() << "\n";
    else if (v.is_float()) {
        // Deliberately the SAME function the JIT's emitted code calls, not
        // std::cout. Two reasons, and the second is the important one:
        //
        //   1. Correctness. `std::cout << double` defaults to 6 significant
        //      digits and prints "0.333333" for 1/3 and "1" for
        //      1.0000000000000002. CPython's repr is the shortest string
        //      that round-trips, so it prints "0.3333333333333333" and
        //      "1.0000000000000002". The interpreter is used as the oracle
        //      for float code, so a lossy default here would make correct
        //      JIT output look like a JIT bug.
        //   2. The whole point of the tiered engine is that both tiers
        //      produce byte-identical stdout. Sharing one formatter makes
        //      that structural instead of a coincidence to be maintained,
        //      and it keeps signed zero and the sign of NaN (which the
        //      stream operator also disagrees with CPython about) in step.
        std::cout << lithon::jit::host_format_double(v.as_float()) << "\n";
    }
    else throw std::runtime_error("interpreter: print() of an unsupported value kind in this slice");
}

// Hardening item 5. Explicit numeric conversions. Both mirror the JIT's
// machine forms exactly so the tiers agree: ToFloat is a (possibly identity)
// widening, ToInt is a truncation-toward-zero with the SAME range test the JIT
// emits (x in [-2^(w-1), 2^(w-1))), which is what makes NaN, +/-inf and an
// out-of-width magnitude trap E0309 identically in both engines.
LithonValue convert_to_float(LithonValue v) {
    if (v.is_float()) return v;
    if (!v.is_int())
        throw std::runtime_error("interpreter: float(...) of a non-numeric value");
    return LithonValue::make_float(static_cast<double>(v.as_int()));
}

LithonValue convert_to_int(LithonValue v, int width) {
    if (!v.is_float())
        throw std::runtime_error("interpreter: int(...) of a non-float value");
    const double x = v.as_float();
    const double bound = std::ldexp(1.0, width - 1);
    if (!(x >= -bound && x < bound)) {
        throw std::runtime_error(lithon::jit::kE0309Message);
    }
    return LithonValue::make_int(static_cast<int64_t>(std::trunc(x)));
}

const Function* find_function(const Module& module, const std::string& name) {
    for (const auto& fn : module.functions) {
        if (fn.name == name) return &fn;
    }
    return nullptr;
}

std::pair<std::string, std::string> split_branch_targets(const std::string& s) {
    size_t comma = s.find(',');
    if (comma == std::string::npos) {
        throw std::runtime_error("interpreter: malformed branch targets: " + s);
    }
    return {s.substr(0, comma), s.substr(comma + 1)};
}

// Executes one function call: builds a fresh Frame, binds arg_values to
// fn.params, walks blocks until Return, and returns the result. Calls
// to user-defined functions recurse into this same routine -- each
// recursive call gets its own Frame on the real C++ call stack, which
// is what makes recursion (fib.py etc.) work correctly with isolated
// locals per call, with no extra machinery needed.
LithonValue execute_function(const Module& module, const Function& fn,
                              const std::vector<LithonValue>& arg_values) {
    if (arg_values.size() != fn.params.size()) {
        throw std::runtime_error("interpreter: argument count mismatch calling '" + fn.name + "'");
    }
    if (fn.blocks.empty()) {
        throw std::runtime_error("interpreter: function '" + fn.name + "' has no blocks");
    }

    // E0401 stack-depth guard. Bumps the SAME shared counter the JIT's
    // prologues bump, and throws the SAME message at the SAME depth, so the
    // two tiers trap identically. The RAII guard is what nests: the counter
    // unwinds with the C++ call stack whether this call returns or throws.
    //
    // The guard must be active BEFORE the increment check's own throw, which
    // is why DepthGuard is constructed first -- its destructor runs during
    // the unwinding of that throw too, leaving the counter where the other
    // 8192 frames' guards put it (back at zero once the whole recursion
    // unwinds).
    struct DepthGuard {
        ~DepthGuard() { --lithon::jit::g_call_depth; }
    } depth_guard;
    if (++lithon::jit::g_call_depth > lithon::jit::kMaxCallDepth) {
        throw std::runtime_error(
            lithon::jit::format_stack_overflow_message(lithon::jit::g_call_depth));
    }

    Frame frame;
    for (size_t i = 0; i < fn.params.size(); ++i) {
        frame.vars[fn.params[i]] = arg_values[i];
    }

    std::unordered_map<std::string, const BasicBlock*> label_to_block;
    for (const auto& block : fn.blocks) {
        label_to_block[block.label] = &block;
    }

    LithonValue return_value = LithonValue::make_none();
    const BasicBlock* cur = &fn.blocks.front();

    while (true) {
        bool jumped = false;
        bool returned = false;

        for (const auto& instr : cur->instrs) {
            switch (instr.op) {
                case Op::ConstInt:
                    frame.regs[instr.result] = LithonValue::make_int(instr.int_imm);
                    break;
                case Op::ConstFloat:
                    frame.regs[instr.result] = LithonValue::make_float(instr.float_imm);
                    break;
                case Op::ConstBool:
                    frame.regs[instr.result] = LithonValue::make_bool(instr.int_imm != 0);
                    break;
                case Op::Load:
                    frame.regs[instr.result] = frame.get_var(instr.name);
                    break;
                case Op::Store:
                    // 4.3. A dict declaration is a valueless store like the other
                    // containers, so it arrives here and not on a dict opcode.
                    // Every bucket starts empty, which is what makes a bare
                    // declaration an empty dict rather than a table full of zero
                    // valued entries.
                    if (is_dict_declaration(instr)) {
                        if (instr.type_width <= 0) {
                            throw std::runtime_error("interpreter: dict '" + instr.name +
                                                     "' declared with a non-positive bucket count");
                        }
                        const size_t n = static_cast<size_t>(instr.type_width);
                        Frame::DictTable t;
                        t.keys.assign(n, LithonValue::make_int(0));
                        t.values.assign(n, LithonValue::make_int(0));
                        t.occupied.assign(n, false);
                        // Recorded once, at the declaration, so the probe does not
                        // have to carry the key type through every call.
                        t.bool_keys = instr.type_key_kind == "bool";
                        t.key_width = instr.type_key_width > 0 ? instr.type_key_width : 64;
                        frame.dicts[instr.name] = std::move(t);
                        break;
                    }
                    if (is_container_declaration(instr)) {
                        if (instr.type_width <= 0) {
                            throw std::runtime_error("interpreter: container '" + instr.name +
                                                     "' declared with a non-positive capacity");
                        }
                        // Re-executing the declaration (inside a loop, say)
                        // resets the container, like rebinding a name to a
                        // fresh list does.
                        frame.lists[instr.name].assign(static_cast<size_t>(instr.type_width),
                                                       zero_of_kind(instr.type_elem_kind));
                        break;
                    }
                    // 4.6. A struct declaration reserves its byte run and zeroes
                    // it. Re-executing (inside a loop) resets it, like a
                    // container. Zero bytes are zero for every field kind.
                    if (is_struct_declaration(instr)) {
                        if (instr.type_width <= 0) {
                            throw std::runtime_error("interpreter: struct '" + instr.name +
                                                     "' declared with a non-positive size");
                        }
                        frame.structs[instr.name].assign(
                            static_cast<size_t>(instr.type_width), 0);
                        break;
                    }
                    // 0.6.10. A valueless scalar or pointer store is a
                    // type-only declaration: the typechecker consumed it, so
                    // there is no value to read into the variable and no
                    // binding to create. Without this, args.at(0) below
                    // throws on hand-written IR.
                    if (instr.args.empty()) break;
                    frame.vars[instr.name] = frame.get_reg(instr.args.at(0));
                    // 4.4. If this variable's address was taken, keep the bytes
                    // behind the pointer in step with the value. Without this
                    // the pointer and the name would be two sources of truth,
                    // and the second writer -- a syscall reading the block --
                    // would see whichever the mirror missed.
                    frame.sync_arena(instr.name, frame.vars[instr.name]);
                    break;
                case Op::DictStore:
                case Op::DictIndex:
                case Op::DictContains: {
                    Frame::DictTable& t = frame.get_dict(
                        instr.name, instr.op == Op::DictStore ? "store into" : "read from");
                    LithonValue key = frame.get_reg(instr.args.at(0));
                    // 4.3. A bool is a legal key, so this accepts one and reads it
                    // through the int accessor, which is where the bool's 0 or 1
                    // lives. Only then is it canonicalized, which is what makes
                    // True and 1 the same key.
                    if (!key.is_int() && !key.is_bool()) {
                        throw std::runtime_error(
                            "interpreter: dict key must be an int or a bool");
                    }
                    bool found = false;
                    const int64_t b = probe_bucket(t, key, &found);
                    if (instr.op == Op::DictStore) {
                        // The typechecker rejects a repeated key, so reaching a
                        // found bucket here would mean the table was built by
                        // hand. Overwriting is the defined answer for that rather
                        // than a second entry, and it keeps the probe total.
                        t.keys[static_cast<size_t>(b)] =
                            LithonValue::make_int(canonical(key, t));
                        t.values[static_cast<size_t>(b)] = frame.get_reg(instr.args.at(1));
                        t.occupied[static_cast<size_t>(b)] = true;
                    } else if (instr.op == Op::DictContains) {
                        frame.regs[instr.result] = LithonValue::make_bool(found);
                    } else {
                        if (!found) {
                            // The text is fixed, with no key in it, for the same
                            // reason the index trap has no numbers: the JIT's
                            // trap passes a literal string and the two tiers are
                            // compared byte-for-byte on stderr.
                            throw std::runtime_error("dict key not found");
                        }
                        frame.regs[instr.result] = t.values[static_cast<size_t>(b)];
                    }
                    break;
                }
                case Op::Len:
                    // N is part of the type, so this is the capacity; there is
                    // no length that can change at run time.
                    frame.regs[instr.result] = LithonValue::make_int(
                        static_cast<int64_t>(frame.get_list(instr.name, "len() of").size()));
                    break;
                // 4.4. A pointer is an address in the synthetic address space.
                // AddressOf hands out the variable's address (first mention, so
                // the same target twice yields the same pointer); valueof
                // resolves it back and returns the variable's live value -- a
                // float for a float variable, so printing matches the native
                // movsd path. A valueof whose address names no live variable is
                // the dangling case, deterministic but out of 4.4's scope.
                case Op::AddressOf:
                    frame.regs[instr.result] =
                        LithonValue::make_int(frame.address_of(instr.name));
                    break;
                // 4.4. A pointer is a real address, so valueof resolves it to
                // the object it names. The value comes from the variable's own
                // slot, which is kept byte-identical to the block a pointer
                // addresses (see sync_arena), so a narrow int is still the
                // signed value it was stored as.
                case Op::ValueOf: {
                    LithonValue ptr = frame.get_reg(instr.args.at(0));
                    if (!ptr.is_int()) {
                        throw std::runtime_error("interpreter: valueof of a non-address value");
                    }
                    frame.regs[instr.result] = frame.get_var(frame.name_behind(ptr.as_int()));
                    break;
                }
                // 4.6. Struct field access. The offset (int_imm) and the field's
                // type come straight from the frontend's layout, so there is no
                // lookup and no bounds check beyond the byte run itself. A float
                // field is stored as its IEEE-754 bits in the same little-endian
                // order the JIT's movsd writes, so a field read back bit-for-bit
                // agrees across the tiers.
                case Op::FieldLoad: {
                    const std::vector<uint8_t>& bytes =
                        frame.get_struct(frame.field_target(instr, 0), "read from");
                    const int off = static_cast<int>(instr.int_imm);
                    if (instr.type_kind == "float") {
                        if (off < 0 || static_cast<size_t>(off) + 8 > bytes.size()) {
                            throw std::runtime_error("interpreter: struct field read out of range");
                        }
                        uint64_t bits = 0;
                        for (int i = 0; i < 8; ++i)
                            bits |= static_cast<uint64_t>(bytes[static_cast<size_t>(off + i)]) << (8 * i);
                        double d = 0.0;
                        std::memcpy(&d, &bits, sizeof(d));
                        frame.regs[instr.result] = LithonValue::make_float(d);
                    } else {
                        const int width = instr.type_kind == "bool" ? 1 : instr.type_width / 8;
                        const int64_t raw = struct_load_int(bytes, off, width,
                                                            instr.type_kind == "bool");
                        frame.regs[instr.result] = instr.type_kind == "bool"
                            ? LithonValue::make_bool(raw != 0)
                            : LithonValue::make_int(raw);
                    }
                    break;
                }
                case Op::FieldStore: {
                    std::vector<uint8_t>& bytes =
                        frame.get_struct(frame.field_target(instr, 1), "write to");
                    const int off = static_cast<int>(instr.int_imm);
                    LithonValue v = frame.get_reg(instr.args.at(0));
                    if (instr.type_kind == "float") {
                        if (off < 0 || static_cast<size_t>(off) + 8 > bytes.size()) {
                            throw std::runtime_error("interpreter: struct field write out of range");
                        }
                        const double d = v.as_float();
                        uint64_t bits = 0;
                        std::memcpy(&bits, &d, sizeof(bits));
                        for (int i = 0; i < 8; ++i)
                            bytes[static_cast<size_t>(off + i)] =
                                static_cast<uint8_t>((bits >> (8 * i)) & 0xFF);
                    } else {
                        const int width = instr.type_kind == "bool" ? 1 : instr.type_width / 8;
                        const int64_t raw = (instr.type_kind == "bool") ? (v.as_int() != 0 ? 1 : 0)
                                                                        : v.as_int();
                        struct_store_int(bytes, off, width, raw);
                    }
                    break;
                }
                // Hardening item 5. Explicit conversions and terminal input.
                case Op::ToFloat:
                    frame.regs[instr.result] =
                        convert_to_float(frame.get_reg(instr.args.at(0)));
                    break;
                case Op::ToInt:
                    frame.regs[instr.result] =
                        convert_to_int(frame.get_reg(instr.args.at(0)), instr.type_width);
                    break;
                case Op::InputInt: {
                    int64_t value = 0;
                    const char* err = nullptr;
                    if (!lithon::jit::read_int_line(instr.name.c_str(), instr.type_width,
                                                    &value, &err)) {
                        throw std::runtime_error(err);
                    }
                    frame.regs[instr.result] = LithonValue::make_int(value);
                    break;
                }
                case Op::GuardNonZero: {
                    LithonValue v = frame.get_reg(instr.args.at(0));
                    if (!v.is_int()) {
                        throw std::runtime_error("interpreter: guardnonzero requires int");
                    }
                    if (v.as_int() == 0) {
                        throw std::runtime_error(lithon::jit::kE0310Message);
                    }
                    break;
                }
                // Phase III. The raw syscall. Both engines reach the same
                // helper, so the platform fork -- the real `syscall`
                // instruction on Linux, a host hook elsewhere -- is identical
                // for the interpreter and the JIT by construction. The result
                // is the kernel's raw return, stored as a plain int[64].
                // Arguments are integers in this slice; the typechecker
                // rejects pointers, whose interpreter representation is a
                // synthetic address rather than real memory.
                case Op::Syscall: {
                    if (instr.args.empty()) {
                        throw std::runtime_error(
                            "interpreter: syscall without a syscall number");
                    }
                    const int64_t number = frame.get_reg(instr.args.at(0)).as_int();
                    int64_t slots[6] = {0, 0, 0, 0, 0, 0};
                    const size_t extra = std::min<size_t>(instr.args.size() - 1, 6);
                    for (size_t i = 0; i < extra; ++i) {
                        slots[i] = frame.get_reg(instr.args.at(i + 1)).as_int();
                    }
                    frame.regs[instr.result] =
                        LithonValue::make_int(lithon::jit::host_syscall(number, slots));
                    break;
                }
                case Op::Index:
                case Op::IndexStore: {
                    std::vector<LithonValue>& elems = frame.get_list(
                        instr.name, instr.op == Op::Index ? "index into" : "index store into");
                    LithonValue idx = frame.get_reg(instr.args.at(0));
                    if (!idx.is_int()) {
                        throw std::runtime_error("interpreter: list index must be an int");
                    }
                    // Negative indices trap on purpose: CPython would wrap
                    // xs[-1] to the last element, Lithon does not. The text is
                    // fixed (no index, no capacity) because the JIT's trap
                    // passes a literal string and stderr is compared
                    // byte-for-byte between the tiers.
                    const int64_t i = idx.as_int();
                    if (i < 0 || i >= static_cast<int64_t>(elems.size())) {
                        throw std::runtime_error("list index out of range");
                    }
                    if (instr.op == Op::Index) {
                        frame.regs[instr.result] = elems[static_cast<size_t>(i)];
                    } else {
                        elems[static_cast<size_t>(i)] = frame.get_reg(instr.args.at(1));
                    }
                    break;
                }
                case Op::Add:
                case Op::Sub:
                case Op::Mul:
                case Op::WrapAdd:
                case Op::WrapSub:
                case Op::WrapMul:
                case Op::Div:
                case Op::Mod:
                    frame.regs[instr.result] = apply_binop(
                        instr.op, frame.get_reg(instr.args.at(0)), frame.get_reg(instr.args.at(1)));
                    break;
                case Op::Lt:
                case Op::Gt:
                case Op::Eq:
                    frame.regs[instr.result] = apply_compare(
                        instr.op, frame.get_reg(instr.args.at(0)), frame.get_reg(instr.args.at(1)));
                    break;
                case Op::And:
                case Op::Or:
                    frame.regs[instr.result] = apply_boolop(
                        instr.op, frame.get_reg(instr.args.at(0)), frame.get_reg(instr.args.at(1)));
                    break;
                case Op::Shl:
                case Op::Shr:
                case Op::BitAnd:
                case Op::BitOr:
                case Op::BitXor:
                    frame.regs[instr.result] = apply_bitop(
                        instr.op, frame.get_reg(instr.args.at(0)), frame.get_reg(instr.args.at(1)));
                    break;
                case Op::Not: {
                    LithonValue v = frame.get_reg(instr.args.at(0));
                    frame.regs[instr.result] = LithonValue::make_bool(!v.is_truthy());
                    break;
                }
                case Op::Call: {
                    if (instr.name == "print") {
                        const lithon::ir::ValueId arg = instr.args.at(0);
                        if (print_arg_is_ptr(module, fn, arg)) do_print_ptr(frame.get_reg(arg));
                        else do_print(frame.get_reg(arg));
                        break;
                    }
                    const Function* callee = find_function(module, instr.name);
                    if (!callee) {
                        throw std::runtime_error("interpreter: unknown call target '" + instr.name + "'");
                    }
                    std::vector<LithonValue> args;
                    for (ValueId id : instr.args) {
                        args.push_back(frame.get_reg(id));
                    }
                    LithonValue result = execute_function(module, *callee, args);
                    if (instr.result != kInvalidValue) {
                        frame.regs[instr.result] = result;
                    }
                    break;
                }
                case Op::Branch: {
                    LithonValue cond = frame.get_reg(instr.args.at(0));
                    auto [then_label, else_label] = split_branch_targets(instr.name);
                    const std::string& target = cond.is_truthy() ? then_label : else_label;
                    auto it = label_to_block.find(target);
                    if (it == label_to_block.end()) {
                        throw std::runtime_error("interpreter: branch to unknown block '" + target + "'");
                    }
                    cur = it->second;
                    jumped = true;
                    break;
                }
                case Op::Jump: {
                    auto it = label_to_block.find(instr.name);
                    if (it == label_to_block.end()) {
                        throw std::runtime_error("interpreter: jump to unknown block '" + instr.name + "'");
                    }
                    cur = it->second;
                    jumped = true;
                    break;
                }
                case Op::Return:
                    if (!instr.args.empty()) {
                        return_value = frame.get_reg(instr.args.at(0));
                    }
                    returned = true;
                    break;
                default:
                    throw std::runtime_error("interpreter: opcode not yet implemented in this slice");
            }
            if (jumped || returned) break;
        }

        if (returned) return return_value;
        if (!jumped) return return_value; // fell off the end -- safety net
    }
}

} // namespace

void run_main(const Module& module) {
    // Entry point: the frontend emits __main__ (so user code may define its own
    // `main`); hand-written IR may still use a plain `main`.
    const Function* main_fn = find_function(module, "__main__");
    if (!main_fn) main_fn = find_function(module, "main");
    if (!main_fn) {
        throw std::runtime_error("interpreter: no '__main__' or 'main' function in module");
    }
    const auto kinds = lithon::jit::infer_value_kinds(module);
    g_value_kinds = &kinds;
    struct Reset { ~Reset() { g_value_kinds = nullptr; } } reset;
    // E0401: a fresh program starts at depth 0; execute_function's own guard
    // bumps it to 1 for __main__.
    lithon::jit::g_call_depth = 0;
    execute_function(module, *main_fn, {});
}

} // namespace lithon::interp
