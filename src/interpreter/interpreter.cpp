#include "interpreter.h"
#include "runtime/value.h"
#include "jit/float_runtime.h"
#include "jit/print_guard.h"
#include "dict_hash.h"

#include <cmath>
#include <cstdio>
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

    // 4.4. Synthetic addresses. The JIT computes a real rbp-relative pointer
    // on the native stack, which an interpreter has no way to reproduce, so
    // the interpreter proves pointer semantics on a synthetic address space
    // of its own: each addressed variable is handed a small, strictly
    // positive, distinct address on first mention, and valueof resolves the
    // address back to the variable's live value through the reverse map.
    //
    // Determinism and uniqueness are the whole contract:
    //   * the addresses depend only on program order, not on machine state,
    //     so `_p == _q` (same target twice) is False, `_p == _q` of two
    //     different variables is False, and `_p == 0` is always False after a
    //     successful addressof -- 0 is never handed out, on purpose, so it
    //     stays the "no pointer" sentinel that the `_p == 0` test means;
    //   * a valueof of an address that resolves to no live variable is a
    //     deterministic trap, which is the interpreter's answer for the
    //     dangling-pointer case. That case is out of scope for 4.4 and never
    //     exercised by the corpus.
    std::unordered_map<std::string, int64_t> addr_of_var;
    std::unordered_map<int64_t, std::string> var_of_addr;
    int64_t synthetic_next = 0x1000;

    int64_t address_of(const std::string& name) {
        auto it = addr_of_var.find(name);
        if (it != addr_of_var.end()) return it->second;
        if (vars.find(name) == vars.end()) {
            throw std::runtime_error("interpreter: reference to undefined variable '" + name + "'");
        }
        const int64_t addr = synthetic_next;
        synthetic_next += 8;
        addr_of_var[name] = addr;
        var_of_addr[addr] = name;
        return addr;
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
        if (b == 0.0) throw std::runtime_error("interpreter: division by zero");
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
                if (b == 0.0) throw std::runtime_error("interpreter: modulo by zero");
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
            if (b == 0) throw std::runtime_error("interpreter: modulo by zero");
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
                throw std::runtime_error("interpreter: shift count out of range 0..63 for `<<`");
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
                throw std::runtime_error("interpreter: shift count out of range 0..63 for `>>`");
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
                    // 0.6.10. A valueless scalar or pointer store is a
                    // type-only declaration: the typechecker consumed it, so
                    // there is no value to read into the variable and no
                    // binding to create. Without this, args.at(0) below
                    // throws on hand-written IR.
                    if (instr.args.empty()) break;
                    frame.vars[instr.name] = frame.get_reg(instr.args.at(0));
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
                            throw std::runtime_error("interpreter: dict key not found");
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
                case Op::ValueOf: {
                    LithonValue ptr = frame.get_reg(instr.args.at(0));
                    if (!ptr.is_int()) {
                        throw std::runtime_error("interpreter: valueof of a non-address value");
                    }
                    auto back = frame.var_of_addr.find(ptr.as_int());
                    if (back == frame.var_of_addr.end()) {
                        throw std::runtime_error(
                            "interpreter: dereference of a pointer with no live variable behind it");
                    }
                    frame.regs[instr.result] = frame.get_var(back->second);
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
                        throw std::runtime_error("interpreter: list index out of range");
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
    execute_function(module, *main_fn, {});
}

} // namespace lithon::interp
