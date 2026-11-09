#pragma once

// x86-32 native backend (4.17.1).
//
// A deliberately simple code generator, written for correctness first: every IR
// value and every variable lives in an 8-byte frame slot (low dword at +0, high at
// +4), because IR temporaries are ALWAYS 64-bit -- narrow widths are only checked
// at `store` -- so a 32-bit machine needs pairs from the first arithmetic op.
// There is no register allocator yet (tracked in the README Todo); every op loads
// its operands into EAX/ECX/EDX, computes, and stores the pair back.
//
// Conventions
//   * JIT-to-JIT calls are cdecl: arguments pushed right to left, each one 8 bytes
//     (high dword pushed first), the caller pops them, the result comes back in
//     EDX:EAX. That is the same on every 32-bit target.
//   * Host (C) calls are cdecl too (printf, the trap reporters, 64-bit helpers).
//     Only EAX/ECX/EDX are used, all caller-saved on both i386 ABIs, so nothing
//     needs saving. kStackAlign is 4 on Win32 and 16 on System V i386 (which
//     requires a 16-byte aligned stack at every call); every call site pads to it.
//   * Semantics mirror compile_function.h exactly: the same E0303 overflow traps,
//     E0310/E0313, shift-count and zero-divisor messages, the E0401 call-depth
//     guard, and the same print formats.
//
// Anything not listed in compile_module below throws, and the caller falls back to
// the interpreter -- the same contract as the x86-64 backend.

#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <deque>
#include <map>
#include <functional>
#include <memory>

#include "compile_function.h"
#include "container_print.h"
#include "runtime/class_runtime.h"
#include "syscall_runtime.h"
#include "ir/ir.h"
#include "input_runtime.h"
#include "loop_info.h"
#include "print_guard.h"
#include "stack_guard.h"
#include "x86_32_encoder.h"

namespace lithon::jit::x86_32 {

#if defined(_WIN32)
inline constexpr int kStackAlign = 4;     // Win32: the stack is only 4-byte aligned
#else
inline constexpr int kStackAlign = 16;    // System V i386: 16-byte aligned at a call
#endif

// ---- host helpers the generated code calls (cdecl) ------------------------
// 64-bit values cross the boundary as two dwords, low first, exactly as a C
// compiler passes an int64_t.

// Returns non-zero on signed overflow. The product goes through *out.
inline int host32_mul64(int64_t a, int64_t b, int64_t* out) {
    return __builtin_mul_overflow(a, b, out) ? 1 : 0;
}
inline int64_t host32_mod64(int64_t a, int64_t b) {
    if (a == INT64_MIN && b == -1) return 0;   // the wrapped answer, as in both other tiers
    return a % b;
}
inline int64_t host32_shl64(int64_t a, int32_t n) {
    return static_cast<int64_t>(static_cast<uint64_t>(a) << n);
}
inline int64_t host32_sar64(int64_t a, int32_t n) { return a >> n; }

// Floats cross the boundary as two dwords too. A double RESULT goes through an
// out-pointer instead of the x87 stack (the i386 convention returns a double in
// ST0, which this backend would otherwise need x87 encodings to read).
inline void host32_i2d(int64_t v, double* out) { *out = static_cast<double>(v); }
// fmod, with a zero result normalised to +0.0 -- what the interpreter does.
inline void host32_fmod(double a, double b, double* out) {
    double r = std::fmod(a, b);
    *out = r == 0.0 ? 0.0 : r;
}
// float -> int[width], truncating toward zero. Returns non-zero (E0309) on NaN, an
// infinity, or a magnitude outside the target width, exactly like compile_function.h.
inline int host32_f2i(double x, int32_t width, int64_t* out) {
    const double bound = std::ldexp(1.0, width - 1);
    if (!(x == x) || x < -bound || x >= bound) return 1;
    *out = static_cast<int64_t>(x);
    return 0;
}

// ---- dictionaries -----------------------------------------------------------
// The x86-64 backend emits the bucket probe inline; here it is a host helper over
// the same frame regions (keys / occupancy / values), with the same hash
// (dict::kHashMultiplier, dict::hash_shift) and the same linear probe, so bucket
// order -- and therefore the order a dict prints in -- is identical.
struct Dict32Desc {
    int32_t buckets, keys_offset, occupied_offset, values_offset;
    int32_t key_stride, value_stride;
    int32_t key_is_str, key_is_bool, key_width;
    int32_t value_is_signed_int, value_is_container;
    DictStrDesc str;
};

// Finds the bucket for `key`. Returns 1 on a hit, 0 on a miss; *bucket is the hit
// bucket, or the free one an insert should take. A full table with no match is a
// miss at the wrapped-around bucket, exactly as in the inline probe.
inline int32_t host32_dict_probe(uint8_t* base, const Dict32Desc* d, int64_t key_in, bool insert,
                                 int32_t* bucket) {
    if (d->key_is_str) {
        const int64_t b = insert ? host_dict_str_insert(base, &d->str) : host_dict_str_find(base, &d->str);
        if (b < 0) return 0;
        *bucket = static_cast<int32_t>(b);
        return 1;
    }
    uint64_t key = static_cast<uint64_t>(key_in);
    if (d->key_is_bool) key = key != 0 ? 1 : 0;
    else if (d->key_width > 0 && d->key_width < 64) key &= (uint64_t{1} << d->key_width) - 1;
    const uint64_t* occupied = reinterpret_cast<const uint64_t*>(base + d->occupied_offset);
    uint32_t idx = 0;
    if (d->buckets > 1) {
        uint64_t h = key * static_cast<uint64_t>(dict::kHashMultiplier);
        const int shift = dict::hash_shift(d->buckets);
        if (shift > 0 && shift < 64) h >>= shift;
        idx = static_cast<uint32_t>(h) & static_cast<uint32_t>(d->buckets - 1);
    }
    for (int32_t steps = 0;;) {
        if (occupied[idx] == 0) { *bucket = static_cast<int32_t>(idx); return 0; }
        uint64_t stored = 0;
        std::memcpy(&stored, base + d->keys_offset + static_cast<size_t>(idx) * d->key_stride,
                    static_cast<size_t>(d->key_stride));
        if (stored == key) { *bucket = static_cast<int32_t>(idx); return 1; }
        idx = (idx + 1) & static_cast<uint32_t>(d->buckets - 1);
        if (++steps >= d->buckets) { *bucket = static_cast<int32_t>(idx); return 0; }
    }
}

// d[key] = value. `src` points at the value's bytes (a value slot or a list run).
inline void host32_dict_store(uint8_t* base, const Dict32Desc* d, int64_t key, const void* src) {
    int32_t bucket = 0;
    host32_dict_probe(base, d, key, true, &bucket);
    std::memcpy(base + d->values_offset + static_cast<size_t>(bucket) * d->value_stride, src,
                static_cast<size_t>(d->value_stride));
    if (!d->key_is_str) {
        uint64_t k = static_cast<uint64_t>(key);
        if (d->key_is_bool) k = k != 0 ? 1 : 0;
        else if (d->key_width > 0 && d->key_width < 64) k &= (uint64_t{1} << d->key_width) - 1;
        std::memcpy(base + d->keys_offset + static_cast<size_t>(bucket) * d->key_stride, &k,
                    static_cast<size_t>(d->key_stride));
    }
    reinterpret_cast<uint64_t*>(base + d->occupied_offset)[bucket] = 1;
}

// d[key] / key in d. Returns 1 when present (copying the value to `dst` when given:
// a scalar is widened to a full 8-byte value, a list run is copied raw).
inline int32_t host32_dict_get(uint8_t* base, const Dict32Desc* d, int64_t key, void* dst) {
    int32_t bucket = 0;
    if (!host32_dict_probe(base, d, key, false, &bucket)) return 0;
    if (dst == nullptr) return 1;
    const uint8_t* v = base + d->values_offset + static_cast<size_t>(bucket) * d->value_stride;
    if (d->value_is_container) {
        std::memcpy(dst, v, static_cast<size_t>(d->value_stride));
    } else {
        uint64_t raw = 0;
        std::memcpy(&raw, v, static_cast<size_t>(d->value_stride));
        if (d->value_is_signed_int && d->value_stride < 8) {
            const int bits = d->value_stride * 8;
            const uint64_t sign = uint64_t{1} << (bits - 1);
            raw = (raw ^ sign) - sign;
        }
        std::memcpy(dst, &raw, 8);
    }
    return 1;
}

namespace {
const char kFmtInt[] = "%lld\n";
const char kFmtIntBare[] = "%lld";
const char kFmtUInt[] = "%llu\n";
const char kFmtUIntBare[] = "%llu";
const char kTrue[] = "True\n";
const char kFalse[] = "False\n";
const char kTrueBare[] = "True";
const char kFalseBare[] = "False";
const char kFmtStr[] = "%s\n";
const char kFmtStrBare[] = "%s";
const char kSep[] = " ";
const char kNl[] = "\n";
}  // namespace

// Kinds a function parameter / return / plain variable may have: all of them are
// one 8-byte pair (an int, bool, float, or a pointer-sized class reference).
inline bool native_scalar_kind(const std::string& k) {
    return k == "int" || k == "bool" || k == "float" || k == "class" || k == "ptr";
}

inline uint32_t addr_of(const void* p) {
    return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(p));
}

// The three C calling conventions a Lithon function can be exported under.
//   cdecl    caller pops; every scalar parameter is one 8-byte slot (an int64_t, or a
//            double bit-for-bit); the result is in EDX:EAX, or ST0 for a double.
//   stdcall  as cdecl, but the callee pops its arguments (`ret 8n`).
//   fastcall Microsoft/GCC fastcall: the first two parameters (int or bool, <= 32 bits)
//            arrive in ECX and EDX, the rest as 8-byte slots on the stack, callee pops.
// The internal JIT-to-JIT convention is cdecl, so for integer results `name$cdecl` is
// the function itself; everything else is a small adapter emitted after the code.
// Thunks are emitted only when asked for (export_thunks), under the names
// `<function>$cdecl`, `<function>$stdcall` and `<function>$fastcall`.
inline CompiledModule compile_module(const ir::Module& module, bool export_thunks = false) {
    using namespace ir;
    if (sizeof(void*) != 4) {
        throw std::runtime_error(
            "compile_module_x86_32: the generated code embeds 32-bit host addresses and "
            "can only be built inside a 32-bit process");
    }
    const auto module_kinds = infer_value_kinds(module);

    Code code;
    std::unordered_map<std::string, size_t> function_offset;
    std::unordered_map<std::string, int> class_index;
    for (size_t ci = 0; ci < module.classes.size(); ++ci) class_index[module.classes[ci].name] = static_cast<int>(ci);
    std::vector<std::string> thunk_pending;
    struct CallPatch { size_t at; std::string callee; };
    std::vector<CallPatch> call_patches;

    for (size_t fi = 0; fi < module.functions.size(); ++fi) {
        const Function& fn = module.functions[fi];
        const std::vector<Kind>& kinds = fi < module_kinds.size() ? module_kinds[fi] : std::vector<Kind>{};
        function_offset[fn.name] = code.size();

        // ---- frame layout -------------------------------------------------
        // The variable layout (scalars, containers, strs, dicts, structs) comes from
        // the SAME allocator the x86-64 backend uses, with promotion off, so a
        // container's element offsets, strides and dict regions are identical by
        // construction -- the print descriptors and helpers that read them cannot
        // disagree between the two. Variables live at alloc.variable_offset(); the
        // incoming parameters are copied there in the prologue. IR values then get
        // 8-byte slots below the allocator's frame.
        for (size_t i = 0; i < fn.params.size(); ++i) {
            if (i < fn.param_type_kinds.size() && !native_scalar_kind(fn.param_type_kinds[i])) {
                throw std::runtime_error("compile_module_x86_32: '" + fn.name +
                                         "' has a " + fn.param_type_kinds[i] + " parameter (not supported yet)");
            }
        }
        if (!fn.return_type_kind.empty() && !native_scalar_kind(fn.return_type_kind)) {
            throw std::runtime_error("compile_module_x86_32: '" + fn.name + "' returns a " +
                                     fn.return_type_kind + " (not supported yet)");
        }
        RegisterAllocator alloc(fn, PromotionMap{}, VirtualTemps{}, /*borrow=*/false);
        ValueId max_value = 0;
        bool any_value = false;
        for (const auto& b : fn.blocks)
            for (const auto& in : b.instrs)
                if (in.result != kInvalidValue) { max_value = std::max(max_value, in.result); any_value = true; }
        const int32_t value_base = alloc.frame_size();   // value v is at -(value_base + 8*(v+1))
        const int32_t n_values = any_value ? static_cast<int32_t>(max_value) + 1 : 0;
        // Two scratch pairs below the values: an int operand of a float op is converted
        // into one before the SSE instruction reads it; one more holds a virtual call's
        // resolved target while its arguments are pushed.
        const int32_t scratch_base = value_base + 8 * n_values;
        auto scratch = [&](int i) -> int32_t { return -(scratch_base + 8 * (i + 1)); };
        // ---- one register-resident variable ------------------------------------
        // The scalar integer variable with the highest loop-weighted use count lives in
        // EBX:ESI for the whole function (callee-saved on both i386 ABIs, so it is saved
        // in the frame and restored before every return, and helpers and callees keep
        // it). Only variables that are touched by plain Load/Store qualify: never
        // address-taken, never a container/struct/dict, never a float, never a
        // parameter (those already sit in their incoming slot).
        std::string hot_var;
        {
            std::unordered_map<std::string, size_t> label_index;
            for (size_t b = 0; b < fn.blocks.size(); ++b) label_index[fn.blocks[b].label] = b;
            std::vector<int> depth(fn.blocks.size(), 0);
            for (size_t b = 0; b < fn.blocks.size(); ++b) {
                if (fn.blocks[b].instrs.empty()) continue;
                const Instr& term = fn.blocks[b].instrs.back();
                if (term.op != Op::Jump && term.op != Op::Branch) continue;
                for (const std::string& t : branch_targets(term)) {
                    const auto li = label_index.find(t);
                    if (li != label_index.end() && li->second <= b)
                        for (size_t k = li->second; k <= b; ++k) ++depth[k];   // a back edge: [target, b] is a loop
                }
            }
            std::unordered_map<std::string, double> weight;
            std::unordered_set<std::string> excluded;
            for (const auto& pn : fn.params) excluded.insert(pn);
            for (size_t b = 0; b < fn.blocks.size(); ++b) {
                const double w = std::pow(10.0, std::min(depth[b], 3));
                for (const auto& hin : fn.blocks[b].instrs) {
                    if (hin.op == Op::Load) {
                        if (hin.result < kinds.size() && kinds[hin.result] == Kind::Float) excluded.insert(hin.name);
                        weight[hin.name] += w;
                    } else if (hin.op == Op::Store) {
                        if (hin.args.size() != 1 || !(hin.type_kind == "int" || hin.type_kind == "bool" || hin.type_kind.empty()))
                            excluded.insert(hin.name);
                        weight[hin.name] += w;
                    } else {
                        // any other instruction naming a variable pins it in memory
                        if (!hin.name.empty()) excluded.insert(hin.name);
                        if (!hin.name2.empty()) excluded.insert(hin.name2);
                        if (!hin.name3.empty()) excluded.insert(hin.name3);
                    }
                }
            }
            double best = 0;
            for (const auto& kv : weight) {
                const std::string& nm = kv.first;
                if (excluded.count(nm) || !alloc.has_variable(nm)) continue;
                if (alloc.container_capacity(nm) > 0 || alloc.struct_size(nm) > 0 || alloc.is_dict(nm)) continue;
                if (nm.find(".__len") != std::string::npos) continue;
                if (kv.second > best || (kv.second == best && nm < hot_var)) { best = kv.second; hot_var = nm; }
            }
            if (best < 3) hot_var.clear();   // not worth saving and restoring two registers
        }
        const bool has_hot = !hot_var.empty();
        int32_t frame = scratch_base + 24 + (has_hot ? 8 : 0);
        const int32_t hot_save = -(scratch_base + 24 + 4);   // EBX at hot_save, ESI at hot_save - 4
        // System V i386: the frame must leave ESP 16-aligned (EBP is 8 mod 16 after
        // the push, so the frame is 8 mod 16).
        if (kStackAlign == 16 && frame % 16 != 8) frame += 8;
        if (kStackAlign != 16) frame = (frame + 3) & ~3;
        // ---- constants as immediates -------------------------------------------
        // A ConstInt/ConstBool whose every use is in an op that reads it through the
        // immediate-aware helpers below is never stored: it is an immediate operand.
        // Any other use keeps the old behaviour (stored in its slot). vslot() throws if
        // asked for the slot of a constant that was not stored, so a missed site
        // surfaces as a refusal in the tests, never as wrong code.
        int cur_op = -1;   // for diagnostics only
        int64_t cur_loc = 0;   // packed source position of the instruction being emitted (0 = none)
        std::unordered_map<ValueId, int64_t> const_val;
        std::unordered_set<ValueId> imm_vals;
        {
            for (const auto& b : fn.blocks)
                for (const auto& cin : b.instrs)
                    if ((cin.op == Op::ConstInt || cin.op == Op::ConstBool || cin.op == Op::ConstFloat) &&
                        cin.result != kInvalidValue) {
                        if (cin.op == Op::ConstFloat) {
                            uint64_t bits;
                            std::memcpy(&bits, &cin.float_imm, sizeof bits);
                            const_val[cin.result] = static_cast<int64_t>(bits);
                        } else {
                            const_val[cin.result] = cin.op == Op::ConstBool ? (cin.int_imm != 0 ? 1 : 0) : cin.int_imm;
                        }
                    }
            std::unordered_set<ValueId> bad;
            auto imm_aware = [&](const Instr& u) {
                switch (u.op) {
                case Op::Add: case Op::Sub: case Op::Mul: case Op::Div: case Op::Mod:
                case Op::WrapAdd: case Op::WrapSub:
                case Op::Lt: case Op::Gt: case Op::Eq: case Op::Ne:
                case Op::BitAnd: case Op::BitOr: case Op::BitXor: case Op::Shl: case Op::Shr:
                case Op::And: case Op::Or: case Op::Not:
                case Op::Store: case Op::IndexStore: case Op::Index: case Op::Return:
                case Op::ToFloat: case Op::GuardNonZero: case Op::UTrap: case Op::Branch:
                    return true;
                case Op::Call: {
                    // print builtins and user functions take arguments through push_value
                    if (u.name == "print" || u.name == "print_bare" || u.name == "print_u" || u.name == "print_bare_u")
                        return true;
                    for (const auto& mf : module.functions) if (mf.name == u.name) return true;
                    return false;
                }
                default:
                    return false;
                }
            };
            for (const auto& b : fn.blocks)
                for (const auto& u : b.instrs)
                    for (ValueId a : u.args)
                        if (const_val.count(a) && !imm_aware(u)) bad.insert(a);
            for (const auto& kv : const_val) if (!bad.count(kv.first)) imm_vals.insert(kv.first);
        }
        auto is_imm = [&](ValueId v) { return imm_vals.count(v) != 0; };
        auto imm_lo = [&](ValueId v) { return static_cast<uint32_t>(const_val.at(v)); };
        auto imm_hi = [&](ValueId v) { return static_cast<uint32_t>(static_cast<uint64_t>(const_val.at(v)) >> 32); };
        auto vslot = [&](ValueId v) -> int32_t {
            if (imm_vals.count(v))
                throw std::logic_error("compile_module_x86_32: slot read of an immediate constant (op " +
                                       std::to_string(cur_op) + " in '" + fn.name + "')");
            return -(value_base + 8 * (static_cast<int32_t>(v) + 1));
        };
        auto ld_lo = [&](R r, ValueId v) { if (is_imm(v)) x86_32::mov_r_imm(code, r, imm_lo(v)); else x86_32::mov_r_m(code, r, vslot(v)); };
        auto ld_hi = [&](R r, ValueId v) { if (is_imm(v)) x86_32::mov_r_imm(code, r, imm_hi(v)); else x86_32::mov_r_m(code, r, vslot(v) + 4); };
        auto alu_lo = [&](Alu op, R r, ValueId v) { if (is_imm(v)) x86_32::alu_r_imm(code, op, r, imm_lo(v)); else x86_32::alu_r_m(code, op, r, vslot(v)); };
        auto alu_hi = [&](Alu op, R r, ValueId v) { if (is_imm(v)) x86_32::alu_r_imm(code, op, r, imm_hi(v)); else x86_32::alu_r_m(code, op, r, vslot(v) + 4); };
        std::unordered_map<std::string, int32_t> param_home;   // a parameter's home is where the caller pushed it
        for (size_t i = 0; i < fn.params.size(); ++i) param_home[fn.params[i]] = 8 + 8 * static_cast<int32_t>(i);
        auto voff = [&](const std::string& name) -> int32_t {
            const auto ph = param_home.find(name);
            if (ph != param_home.end()) return ph->second;
            if (!alloc.has_variable(name))
                throw std::runtime_error("compile_module_x86_32: unknown variable '" + name + "' in '" + fn.name + "'");
            return alloc.variable_offset(name);
        };

        // ---- emission helpers ----------------------------------------------
        std::unordered_map<std::string, size_t> block_offset;
        struct JumpPatch { size_t at; std::string label; };
        std::vector<JumpPatch> jump_patches;

        // A host call: pad to kStackAlign, push the arguments (given by `push`,
        // which reports how many bytes it pushed), call, pop everything.
        // Debug aid (LITHON_CHECK_ALIGN=1, System V only): before every call, trap with
        // int3 unless ESP is 16-byte aligned. glibc rarely faults on a misaligned stack,
        // so without this the alignment padding below could be wrong and still pass.
        static const bool check_align = kStackAlign == 16 && std::getenv("LITHON_CHECK_ALIGN") != nullptr;
        auto align_probe = [&]() {
            if (!check_align) return;
            for (uint8_t b : {0xF7, 0xC4, 0x0F, 0x00, 0x00, 0x00}) code.push_back(b);   // test esp, 0xF
            const size_t ok = x86_32::jcc_rel32(code, Cc::E);
            code.push_back(0xCC);                                                          // int3
            x86_32::patch_rel32(code, ok, code.size());
        };
        auto host_call = [&](const void* target, int arg_bytes, auto&& push) {
            const int pad = (kStackAlign - arg_bytes % kStackAlign) % kStackAlign;
            x86_32::sub_esp(code, pad);
            push();
            align_probe();
            x86_32::mov_r_imm(code, R::EAX, addr_of(target));
            x86_32::call_r(code, R::EAX);
            x86_32::add_esp(code, arg_bytes + pad);
        };
        // 6.10. Inside a protected region a trap jumps to the matching handler block
        // (innermost first) instead of reporting; the kind comes from the same
        // classifier the interpreter and the x86-64 backend use.
        struct TryRegion {
            std::vector<std::string> kinds, labels;
            std::string handler_for(const std::string& kind) const {
                for (size_t i = 0; i < kinds.size(); ++i)
                    if (kinds[i] == kind || kinds[i] == "Exception") return labels[i];
                return "";
            }
        };
        std::vector<std::vector<TryRegion>> block_regions(fn.blocks.size());
        {
            std::vector<TryRegion> live;
            for (size_t b = 0; b < fn.blocks.size(); ++b) {
                block_regions[b] = live;
                for (const auto& tin : fn.blocks[b].instrs) {
                    if (tin.op == Op::TryEnter) {
                        TryRegion r;
                        r.kinds = split_marks(tin.name);
                        r.labels = split_marks(tin.name2);
                        live.push_back(std::move(r));
                    } else if (tin.op == Op::TryLeave) {
                        if (!live.empty()) live.pop_back();
                    }
                }
            }
        }
        std::vector<TryRegion> current_regions;
        auto handler_label = [&](const std::string& kind) -> std::string {
            for (size_t r = current_regions.size(); r-- > 0;) {
                const std::string t = current_regions[r].handler_for(kind);
                if (!t.empty()) return t;
            }
            return "";
        };
        auto trap = [&](const char* message) {
            const std::string kind = classify_trap_message(message);
            if (!kind.empty()) {
                const std::string target = handler_label(kind);
                if (!target.empty()) {
                    jump_patches.push_back({x86_32::jmp_rel32(code), target});
                    return;
                }
            }
            host_call(reinterpret_cast<const void*>(&host_report_error), 12, [&] {
                x86_32::push_imm32(code, static_cast<uint32_t>(static_cast<uint64_t>(cur_loc) >> 32));
                x86_32::push_imm32(code, static_cast<uint32_t>(cur_loc));
                x86_32::push_imm32(code, addr_of(message));
            });
        };
        // trap_if(cc, msg): jump out of line when `cc` holds. The jump goes straight to a
        // catching handler block when a try region catches this kind; otherwise to one
        // shared stub per message at the end of the function, so the common (no trap)
        // path has no taken branch and no inline error code.
        struct StubSites { const char* message = nullptr; int64_t loc = 0; std::vector<size_t> sites; };   // the message is a static literal
        std::map<std::string, StubSites> stub_sites;
        auto trap_if = [&](Cc cc, const char* message) {
            const std::string kind = classify_trap_message(message);
            if (!kind.empty()) {
                const std::string target = handler_label(kind);
                if (!target.empty()) {
                    jump_patches.push_back({x86_32::jcc_rel32(code, cc), target});
                    return;
                }
            }
            // One stub per (message, position): each carries the position of ITS site.
            StubSites& st = stub_sites[std::string(message) + "@" + std::to_string(cur_loc)];
            st.message = message;
            st.loc = cur_loc;
            st.sites.push_back(x86_32::jcc_rel32(code, cc));
        };
        auto load_pair = [&](ValueId v, R lo, R hi) { ld_lo(lo, v); ld_hi(hi, v); };
        auto store_pair = [&](ValueId v) {   // result in EDX:EAX
            x86_32::mov_m_r(code, vslot(v), R::EAX);
            x86_32::mov_m_r(code, vslot(v) + 4, R::EDX);
        };
        auto store_flag = [&](ValueId v) {   // 0/1 in EAX
            x86_32::mov_m_r(code, vslot(v), R::EAX);
            x86_32::mov_m_imm(code, vslot(v) + 4, 0);
        };
        auto push_value = [&](ValueId v) {
            if (is_imm(v)) { x86_32::push_imm32(code, imm_hi(v)); x86_32::push_imm32(code, imm_lo(v)); }
            else { x86_32::push_m(code, vslot(v) + 4); x86_32::push_m(code, vslot(v)); }
        };
        // EAX = low | high  (zero iff the 64-bit value is zero); sets ZF.
        auto test_nonzero = [&](ValueId v) {
            if (is_imm(v)) { x86_32::mov_r_imm(code, R::EAX, imm_lo(v) | imm_hi(v)); x86_32::test_r_r(code, R::EAX, R::EAX); return; }
            x86_32::mov_r_m(code, R::EAX, vslot(v));
            x86_32::alu_r_m(code, Alu::Or, R::EAX, vslot(v) + 4);
        };
        auto is_float = [&](ValueId v) { return v < kinds.size() && kinds[v] == Kind::Float; };
        // The frame offset of `v` as a double: itself when it already is one, else
        // an int converted into scratch pair `which` (host call, so do this BEFORE
        // loading any XMM register).
        // Float constants live in a process-lifetime pool, so an SSE instruction reads
        // them straight from an absolute address (the code runs in-process).
        auto as_double = [&](ValueId v, int which) -> x86_32::Mem {
            if (is_float(v)) {
                if (is_imm(v)) {
                    static std::deque<uint64_t> pool;
                    pool.push_back(static_cast<uint64_t>(const_val.at(v)));
                    return x86_32::at_abs(addr_of(&pool.back()));
                }
                return x86_32::at_ebp(vslot(v));
            }
            host_call(reinterpret_cast<const void*>(&host32_i2d), 12, [&] {
                x86_32::lea_r_m(code, R::EAX, scratch(which));
                x86_32::push_r(code, R::EAX);
                push_value(v);
            });
            return x86_32::at_ebp(scratch(which));
        };
        auto mem_plus = [](x86_32::Mem m, int d) { m.disp += d; return m; };
        // Element / field access. A value is an 8-byte pair; a narrower memory cell is
        // sign-extended (ints) or zero-extended (bools) into one.
        auto mem_load = [&](ValueId result, int bytes, bool is_signed, const x86_32::Mem& m) {
            x86_32::Mem hi = m;
            hi.disp += 4;
            switch (bytes) {
            case 8: x86_32::mov_r_mem(code, R::EAX, m); x86_32::mov_r_mem(code, R::EDX, hi); break;
            case 4: x86_32::mov_r_mem(code, R::EAX, m); break;
            case 2: is_signed ? x86_32::movsx_r_mem16(code, R::EAX, m) : x86_32::movzx_r_mem16(code, R::EAX, m); break;
            case 1: is_signed ? x86_32::movsx_r_mem8(code, R::EAX, m) : x86_32::movzx_r_mem8(code, R::EAX, m); break;
            default: throw std::runtime_error("compile_module_x86_32: unsupported element width");
            }
            if (bytes != 8) {
                if (is_signed) x86_32::cdq(code); else x86_32::xor_zero(code, R::EDX);
            }
            store_pair(result);
        };
        auto mem_store = [&](const x86_32::Mem& m, ValueId v, int bytes) {
            x86_32::Mem hi = m;
            hi.disp += 4;
            ld_lo(R::EAX, v);
            switch (bytes) {
            case 8: ld_hi(R::EDX, v);
                    x86_32::mov_mem_r(code, m, R::EAX); x86_32::mov_mem_r(code, hi, R::EDX); break;
            case 4: x86_32::mov_mem_r(code, m, R::EAX); break;
            case 2: x86_32::mov_mem16_r(code, m, R::EAX); break;
            case 1: x86_32::mov_mem8_r(code, m, R::EAX); break;
            default: throw std::runtime_error("compile_module_x86_32: unsupported element width");
            }
        };
        const char* const kIndexTrap = "error: list index out of range\n";
        // ECX = idx, trapping unless 0 <= idx < cap. A 64-bit index is in range exactly
        // when its high dword is 0 and its low dword is below cap (unsigned).
        auto idx_checked_to = [&](R dst, ValueId idx, int cap) {
            ld_hi(dst, idx);
            x86_32::test_r_r(code, dst, dst);
            trap_if(Cc::NE, kIndexTrap);
            ld_lo(dst, idx);
            x86_32::alu_r_imm(code, Alu::Cmp, dst, static_cast<uint32_t>(cap));
            trap_if(Cc::AE, kIndexTrap);
        };
        // ECX = the row-major flat LEAF index of a nested access (every level checked
        // against its own capacity).
        auto flat_index_to_ecx = [&](const Instr& ix, const ContainerLayout& lay, const Type& type, size_t n_idx) {
            if (n_idx != lay.strides.size())
                throw std::runtime_error("compile_module_x86_32: nested index chain does not reach a leaf");
            x86_32::xor_zero(code, R::ECX);
            const Type* cur = &type;
            for (size_t k = 0; k < n_idx; ++k) {
                idx_checked_to(R::EAX, ix.args.at(k), cur->cap);
                const int leaf_stride = lay.strides[k] / lay.leaf_bytes;
                if (leaf_stride != 1) x86_32::imul_r_r_imm32(code, R::EAX, R::EAX, leaf_stride);
                x86_32::alu_r_r(code, Alu::Add, R::ECX, R::EAX);
                cur = cur->elem.get();
            }
        };
        auto zero_frame = [&](int32_t off, int bytes) {
            const int dwords = (bytes + 3) / 4;
            if (dwords <= 16) {
                for (int i = 0; i < dwords; ++i) x86_32::mov_m_imm(code, off + 4 * i, 0);
            } else {
                x86_32::mov_r_imm(code, R::ECX, static_cast<uint32_t>(dwords));
                const size_t top = code.size();
                x86_32::mov_mem_imm(code, x86_32::at_ebp_idx(off - 4, R::ECX, 4), 0);
                x86_32::alu_r_imm(code, Alu::Sub, R::ECX, 1);
                const size_t again = x86_32::jcc_rel32(code, Cc::NE);
                x86_32::patch_rel32(code, again, top);
            }
        };
        // helper(base, desc): base is EBP itself or EBP + offset.
        auto call_desc = [&](const void* fn_ptr, int32_t base_off, const void* desc) {
            host_call(fn_ptr, 8, [&] {
                x86_32::push_imm32(code, addr_of(desc));
                if (base_off == 0) {
                    x86_32::push_r(code, R::EBP);
                } else {
                    x86_32::lea_r_m(code, R::EAX, base_off);
                    x86_32::push_r(code, R::EAX);
                }
            });
        };
        auto class_ptr_call = [&](const void* fn_ptr, ValueId obj, int extra_bytes, auto&& push_extra) {
            host_call(fn_ptr, 4 + extra_bytes, [&] {
                push_extra();
                x86_32::push_m(code, vslot(obj));   // the pointer is the low dword
            });
        };
        // printing a container / str / dict: the same descriptors the x86-64 backend builds
        // (they are host structs, not machine code), with EBP as the frame base.
        auto emit_print_container = [&](const Instr& pc) {
            if (pc.type_kind == "dict") {
                const auto& d = alloc.dict_layout(pc.name);
                if (d.buckets <= 0) throw std::runtime_error("compile_module_x86_32: print of '" + pc.name + "' which is not a dict");
                ContainerMappingDesc desc{};
                desc.buckets = d.buckets;
                desc.keys_offset = d.keys_offset;
                desc.occupied_offset = d.occupied_offset;
                desc.values_offset = d.values_offset;
                desc.key_stride = d.key_stride;
                desc.value_stride = d.value_stride;
                desc.key_code = leaf_code_of(pc.type_key_kind);
                desc.key_width = pc.type_key_width;
                desc.value_code = leaf_code_of(pc.type_elem_kind);
                desc.value_width = pc.type_elem_width;
                desc.key_is_str = d.key_kind == "str" ? 1 : 0;
                desc.value_is_flat = 0;
                desc.value_flat = nullptr;
                if (d.value_kind == "list" || d.value_kind == "tuple") {
                    if (!pc.decl_type || !pc.decl_type->elem)
                        throw std::runtime_error("compile_module_x86_32: print of dict '" + pc.name + "' has a container value with no type");
                    const ContainerLayout vlay = container_layout(*pc.decl_type->elem);
                    if (!vlay.valid) throw std::runtime_error("compile_module_x86_32: dict value has no flat layout");
                    std::function<const FlatPrintNode*(const Type&, size_t)> vbuild =
                        [&](const Type& t, size_t level) -> const FlatPrintNode* {
                        FlatPrintNode node{};
                        node.len_offset = kNoLenOffset;
                        if (t.kind == "list" || t.kind == "tuple") {
                            node.is_leaf = 0;
                            node.is_tuple = (t.kind == "tuple") ? 1 : 0;
                            node.count = t.cap;
                            node.byte_stride = vlay.strides.at(level);
                            node.child = vbuild(*t.elem, level + 1);
                        } else if (t.kind == "str") {
                            node.is_leaf = 1;
                            node.code = static_cast<int>(LeafCode::Str);
                            node.width = t.cap;
                        } else {
                            node.is_leaf = 1;
                            node.code = leaf_code_of(t.kind);
                            node.width = (t.kind == "bool") ? 8 : t.width;
                        }
                        return register_print_desc(node);
                    };
                    desc.value_is_flat = 1;
                    desc.value_flat = vbuild(*pc.decl_type->elem, 0);
                }
                call_desc(reinterpret_cast<const void*>(&host_print_mapping), 0, register_print_desc(desc));
                return;
            }
            if (alloc.has_flat_layout(pc.name)) {
                const auto* lay = alloc.flat_layout(pc.name);
                const auto type = alloc.container_type(pc.name);
                const int base0 = alloc.element_offset(pc.name, 0);
                int64_t root_len_off = kNoLenOffset;
                if (!pc.name2.empty()) root_len_off = static_cast<int64_t>(alloc.variable_offset(pc.name2)) - base0;
                std::function<const FlatPrintNode*(const Type&, size_t)> build =
                    [&](const Type& t, size_t level) -> const FlatPrintNode* {
                    FlatPrintNode node{};
                    node.len_offset = kNoLenOffset;
                    if (t.kind == "list" || t.kind == "tuple") {
                        node.is_leaf = 0;
                        node.is_tuple = (t.kind == "tuple") ? 1 : 0;
                        node.count = t.cap;
                        node.byte_stride = lay->strides.at(level);
                        if (level == 0) node.len_offset = root_len_off;
                        node.child = build(*t.elem, level + 1);
                    } else if (t.kind == "str") {
                        node.is_leaf = 1;
                        node.code = static_cast<int>(LeafCode::Str);
                        node.width = t.cap;
                    } else {
                        node.is_leaf = 1;
                        node.code = leaf_code_of(t.kind);
                        node.width = (t.kind == "bool") ? 8 : t.width;
                    }
                    return register_print_desc(node);
                };
                call_desc(reinterpret_cast<const void*>(&host_print_flat), base0, build(*type, 0));
                return;
            }
            if (pc.type_kind == "str") {
                const int cap = alloc.container_capacity(pc.name);
                if (cap <= 0) throw std::runtime_error("compile_module_x86_32: print of '" + pc.name + "' which is not a str");
                ContainerStringDesc desc{};
                desc.cap = cap;
                call_desc(reinterpret_cast<const void*>(&host_print_string), alloc.element_offset(pc.name, 0), register_print_desc(desc));
                return;
            }
            const int cap = alloc.container_capacity(pc.name);
            if (cap == 0) throw std::runtime_error("compile_module_x86_32: print of '" + pc.name + "' which is not a container");
            const int base = alloc.element_offset(pc.name, 0);
            ContainerSequenceDesc desc{};
            desc.count = cap;
            desc.stride = alloc.container_stride(pc.name);
            desc.code = leaf_code_of(pc.type_elem_kind);
            desc.width = pc.type_elem_width;
            desc.is_tuple = (pc.type_kind == "tuple") ? 1 : 0;
            desc.len_offset = kNoLenOffset;
            if (!pc.name2.empty()) desc.len_offset = static_cast<int64_t>(alloc.variable_offset(pc.name2)) - base;
            call_desc(reinterpret_cast<const void*>(&host_print_sequence), base, register_print_desc(desc));
        };
        // ---- forwarding and flag fusion ---------------------------------------------
        // A value that is produced and consumed by the very next instruction needs no
        // frame slot: it stays in EAX:EDX (`pending`). An integer compare whose only use
        // is the next Branch leaves its result in the flags (`fused_flag`).
        std::unordered_map<ValueId, int> use_count;
        for (const auto& b : fn.blocks)
            for (const auto& u : b.instrs)
                for (ValueId a : u.args) ++use_count[a];
        ValueId pending = kInvalidValue;
        ValueId fused_flag = kInvalidValue;
        Cc fused_cc = Cc::E;
        bool cur_fwd = false;
        auto flush_pending = [&]() {
            if (pending != kInvalidValue) {
                x86_32::mov_m_r(code, vslot(pending), R::EAX);
                x86_32::mov_m_r(code, vslot(pending) + 4, R::EDX);
                pending = kInvalidValue;
            }
        };
        // EAX:EDX = value a (already there when it was forwarded)
        auto take_a = [&](ValueId a) {
            if (pending == a) { pending = kInvalidValue; return; }
            ld_lo(R::EAX, a);
            ld_hi(R::EDX, a);
        };
        auto finish_pair = [&](ValueId r) {
            if (cur_fwd) pending = r;
            else x86_32::mov_m_r(code, vslot(r), R::EAX), x86_32::mov_m_r(code, vslot(r) + 4, R::EDX);
        };
        // Can instruction `nx` consume forwarded value `r` straight from EAX:EDX?
        auto takes_pending = [&](const Instr& nx, ValueId r) -> bool {
            switch (nx.op) {
            case Op::Add: case Op::Sub: case Op::WrapAdd: case Op::WrapSub:
            case Op::BitAnd: case Op::BitOr: case Op::BitXor:
                return nx.args.size() == 2 && nx.args[0] == r && nx.args[1] != r && !is_float(nx.result) &&
                       !is_float(nx.args[0]) && !is_float(nx.args[1]);
            case Op::Lt: case Op::Gt: case Op::Eq: case Op::Ne:
                return nx.args.size() == 2 && nx.args[0] == r && nx.args[1] != r &&
                       !is_float(nx.args[0]) && !is_float(nx.args[1]);
            case Op::Store:
                return nx.args.size() == 1 && nx.args[0] == r && (nx.type_kind.empty() || native_scalar_kind(nx.type_kind));
            case Op::Return:
                return nx.args.size() == 1 && nx.args[0] == r;
            default:
                return false;
            }
        };
        // The same idea for doubles: a float produced and consumed by the next float
        // instruction stays in XMM0.
        ValueId pending_f = kInvalidValue;
        bool cur_fwd_f = false;
        auto flush_pending_f = [&]() {
            if (pending_f != kInvalidValue) {
                x86_32::movsd_m_x(code, vslot(pending_f), 0);
                pending_f = kInvalidValue;
            }
        };
        auto takes_pending_f = [&](const Instr& nx, ValueId r) -> bool {
            switch (nx.op) {
            case Op::Add: case Op::Sub: case Op::Mul: case Op::Div:
                // `b` must already be a double: converting an int operand calls a helper,
                // which would clobber XMM0.
                return nx.args.size() == 2 && nx.args[0] == r && nx.args[1] != r && is_float(nx.result) &&
                       is_float(nx.args[1]);
            case Op::Store:
                return nx.args.size() == 1 && nx.args[0] == r && nx.type_kind == "float";
            default:
                return false;
            }
        };
        // statically: does this 64-bit constant fit a sign-extended 32-bit value?
        auto fits32 = [&](ValueId v) {
            return is_imm(v) && static_cast<int64_t>(static_cast<int32_t>(const_val.at(v))) == const_val.at(v);
        };
        // ECX = v when v is a sign-extended 32-bit value, else jump to the returned patch
        // list (the slow path). Clobbers EAX, EDX.
        auto check_fits32_into = [&](R dst, ValueId v, std::vector<size_t>& slow) {
            if (is_imm(v)) { x86_32::mov_r_imm(code, dst, imm_lo(v)); return; }
            x86_32::mov_r_m(code, R::EAX, vslot(v));
            x86_32::cdq(code);
            x86_32::alu_r_m(code, Alu::Cmp, R::EDX, vslot(v) + 4);
            slow.push_back(x86_32::jcc_rel32(code, Cc::NE));
            if (dst != R::EAX) x86_32::mov_r_r(code, dst, R::EAX);
        };
        const uint32_t depth_addr = addr_of(&g_call_depth);

        // ---- prologue + E0401 call-depth guard ----------------------------------
        x86_32::prologue(code, frame);
        // The depth never gets near 2^32, so only the low dword of g_call_depth is touched.
        x86_32::alu_abs_imm8(code, Alu::Add, depth_addr, 1);
        x86_32::mov_r_abs(code, R::EAX, depth_addr);
        x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, static_cast<uint32_t>(kMaxCallDepth));
        const size_t depth_ok = x86_32::jcc_rel32(code, Cc::LE);
        host_call(reinterpret_cast<const void*>(&host_report_stack_overflow), 8, [&] {
            x86_32::push_imm32(code, 0);
            x86_32::push_r(code, R::EAX);
        });
        x86_32::patch_rel32(code, depth_ok, code.size());
        if (has_hot) {
            x86_32::mov_m_r(code, hot_save, R::EBX);
            x86_32::mov_m_r(code, hot_save - 4, R::ESI);
        }

        auto emit_return_tail = [&] {
            if (has_hot) {   // EAX:EDX hold the result; EBX/ESI go back to the caller's values
                x86_32::mov_r_m(code, R::EBX, hot_save);
                x86_32::mov_r_m(code, R::ESI, hot_save - 4);
            }
            x86_32::alu_abs_imm8(code, Alu::Sub, depth_addr, 1);
            x86_32::epilogue(code);
            x86_32::ret(code);
        };

        for (size_t bi = 0; bi < fn.blocks.size(); ++bi) {
            const BasicBlock& block = fn.blocks[bi];
            block_offset[block.label] = code.size();
            current_regions = block_regions[bi];
            const std::string next_label = bi + 1 < fn.blocks.size() ? fn.blocks[bi + 1].label : "";
            for (size_t pos = 0; pos < block.instrs.size(); ++pos) {
                const Instr& in = block.instrs[pos];
                cur_op = static_cast<int>(in.op);
                cur_loc = lithon::ir::pack_pos(in.src, in.line);
                if (pending != kInvalidValue && !takes_pending(in, pending)) flush_pending();
                if (pending_f != kInvalidValue && !takes_pending_f(in, pending_f)) flush_pending_f();
                cur_fwd_f = false;
                if ((in.op == Op::Add || in.op == Op::Sub || in.op == Op::Mul || in.op == Op::Div) &&
                    is_float(in.result) && pos + 1 < block.instrs.size()) {
                    const auto uc = use_count.find(in.result);
                    cur_fwd_f = uc != use_count.end() && uc->second == 1 &&
                                takes_pending_f(block.instrs[pos + 1], in.result);
                }
                cur_fwd = false;
                if (in.result != kInvalidValue && pos + 1 < block.instrs.size()) {
                    const auto uc = use_count.find(in.result);
                    if (uc != use_count.end() && uc->second == 1 && takes_pending(block.instrs[pos + 1], in.result)) {
                        switch (in.op) {
                        case Op::Load: case Op::Add: case Op::Sub: case Op::WrapAdd: case Op::WrapSub:
                        case Op::BitAnd: case Op::BitOr: case Op::BitXor: case Op::Shl: case Op::Shr:
                            cur_fwd = !is_float(in.result); break;
                        default: break;
                        }
                    }
                }
                auto unsupported = [&](const char* what) {
                    throw std::runtime_error(std::string("compile_module_x86_32: ") + what +
                                             " is not supported yet (in '" + fn.name + "')");
                };
                switch (in.op) {
                case Op::ConstInt:
                    if (is_imm(in.result)) break;
                    x86_32::mov_m_imm(code, vslot(in.result), static_cast<uint32_t>(in.int_imm));
                    x86_32::mov_m_imm(code, vslot(in.result) + 4, static_cast<uint32_t>(static_cast<uint64_t>(in.int_imm) >> 32));
                    break;
                case Op::ConstFloat: {
                    if (is_imm(in.result)) break;
                    uint64_t bits;
                    std::memcpy(&bits, &in.float_imm, sizeof bits);
                    x86_32::mov_m_imm(code, vslot(in.result), static_cast<uint32_t>(bits));
                    x86_32::mov_m_imm(code, vslot(in.result) + 4, static_cast<uint32_t>(bits >> 32));
                    break;
                }
                case Op::ConstBool:
                    if (is_imm(in.result)) break;
                    x86_32::mov_m_imm(code, vslot(in.result), in.int_imm != 0 ? 1u : 0u);
                    x86_32::mov_m_imm(code, vslot(in.result) + 4, 0);
                    break;
                case Op::Load: {
                    if (has_hot && in.name == hot_var) {
                        x86_32::mov_r_r(code, R::EAX, R::EBX);
                        x86_32::mov_r_r(code, R::EDX, R::ESI);
                    } else {
                        x86_32::mov_r_m(code, R::EAX, voff(in.name));
                        x86_32::mov_r_m(code, R::EDX, voff(in.name) + 4);
                    }
                    finish_pair(in.result);
                    break;
                }
                case Op::Store: {
                    if (in.args.empty()) {
                        // A declaration: zero the storage (the interpreter's containers
                        // start zeroed, and a frame slot holds whatever the stack held).
                        if (in.type_kind == "dict") {
                            const auto& d = alloc.dict_layout(in.name);
                            if (d.buckets > 0) zero_frame(d.occupied_offset, d.buckets * 8);
                        } else if (alloc.has_flat_layout(in.name)) {
                            const auto* lay = alloc.flat_layout(in.name);
                            zero_frame(alloc.element_offset(in.name, 0), ((lay->total_bytes + 7) / 8) * 8);
                        } else if (in.type_kind == "list" || in.type_kind == "tuple" || in.type_kind == "str") {
                            const int cap = alloc.container_capacity(in.name);
                            if (cap > 0)
                                zero_frame(alloc.element_offset(in.name, 0),
                                           ((cap * alloc.container_stride(in.name) + 7) / 8) * 8);
                        } else if (alloc.struct_size(in.name) > 0) {
                            zero_frame(voff(in.name), ((alloc.struct_size(in.name) + 7) / 8) * 8);
                        } else if (in.type_kind == "class" || in.type_kind == "ptr") {
                            zero_frame(voff(in.name), 8);   // a null reference
                        } else {
                            unsupported(("a declaration of a " + in.type_kind).c_str());
                        }
                        break;
                    }
                    if (!(in.type_kind.empty() || native_scalar_kind(in.type_kind)))
                        unsupported(("a " + in.type_kind + " variable").c_str());
                    const int32_t off = voff(in.name);
                    if (pending_f == in.args.at(0)) {
                        x86_32::movsd_m_x(code, off, 0);
                        pending_f = kInvalidValue;
                        break;
                    }
                    take_a(in.args.at(0));
                    if (has_hot && in.name == hot_var) {
                        x86_32::mov_r_r(code, R::EBX, R::EAX);
                        x86_32::mov_r_r(code, R::ESI, R::EDX);
                        break;
                    }
                    x86_32::mov_m_r(code, off, R::EAX);
                    x86_32::mov_m_r(code, off + 4, R::EDX);
                    break;
                }
                case Op::Add: case Op::Sub: case Op::Mul: case Op::Div: case Op::Mod:
                    if (is_float(in.result)) {
                        const bool a_fwd = pending_f == in.args.at(0);
                        const x86_32::Mem a = a_fwd ? x86_32::at_ebp(0) : as_double(in.args.at(0), 0);
                        const x86_32::Mem b = as_double(in.args.at(1), 1);
                        if (in.op == Op::Div || in.op == Op::Mod) {
                            // Python raises for any zero divisor, -0.0 included, and a
                            // NaN divisor propagates. +-0.0 are exactly the bit patterns
                            // whose low dword is 0 and whose high dword is 0 or 0x80000000.
                            x86_32::mov_r_mem(code, R::EAX, mem_plus(b, 4));
                            x86_32::alu_r_imm(code, Alu::And, R::EAX, 0x7fffffffu);
                            x86_32::alu_r_mem(code, Alu::Or, R::EAX, b);
                            trap_if(Cc::E, in.op == Op::Div ? "error: division by zero\n" : "error: modulo by zero\n");
                        }
                        if (in.op == Op::Mod) {
                            host_call(reinterpret_cast<const void*>(&host32_fmod), 20, [&] {
                                x86_32::lea_r_m(code, R::EAX, vslot(in.result));
                                x86_32::push_r(code, R::EAX);
                                x86_32::push_mem(code, mem_plus(b, 4));
                                x86_32::push_mem(code, b);
                                x86_32::push_mem(code, mem_plus(a, 4));
                                x86_32::push_mem(code, a);
                            });
                            break;
                        }
                        if (a_fwd) pending_f = kInvalidValue; else x86_32::movsd_x_mem(code, 0, a);
                        if (in.op == Op::Add) x86_32::addsd_x_mem(code, 0, b);
                        else if (in.op == Op::Sub) x86_32::subsd_x_mem(code, 0, b);
                        else if (in.op == Op::Mul) x86_32::mulsd_x_mem(code, 0, b);
                        else x86_32::divsd_x_mem(code, 0, b);
                        if (cur_fwd_f) pending_f = in.result;
                        else x86_32::movsd_m_x(code, vslot(in.result), 0);
                        break;
                    }
                    if (in.op == Op::Div) unsupported("'/' on non-float operands");
                    [[fallthrough]];
                case Op::WrapAdd: case Op::WrapSub: case Op::WrapMul: {
                    if (in.op == Op::Mul || in.op == Op::WrapMul) {
                        if (in.op == Op::Mul) {
                            // Fast path: both operands are sign-extended 32-bit values (the
                            // overwhelmingly common case), so one imul gives the exact 64-bit
                            // product and it cannot overflow. Anything else takes the helper.
                            const ValueId ma = in.args.at(0), mb = in.args.at(1);
                            std::vector<size_t> slow;
                            size_t done = 0;
                            const bool try_fast = (!is_imm(ma) || fits32(ma)) && (!is_imm(mb) || fits32(mb));
                            if (try_fast) {
                                check_fits32_into(R::ECX, mb, slow);
                                check_fits32_into(R::EAX, ma, slow);
                                if (is_imm(ma) && false) {}
                                x86_32::imul_r1(code, R::ECX);
                                x86_32::mov_m_r(code, vslot(in.result), R::EAX);
                                x86_32::mov_m_r(code, vslot(in.result) + 4, R::EDX);
                                done = x86_32::jmp_rel32(code);
                                for (size_t at : slow) x86_32::patch_rel32(code, at, code.size());
                            }
                            host_call(reinterpret_cast<const void*>(&host32_mul64), 20, [&] {
                                x86_32::lea_r_m(code, R::EAX, vslot(in.result));
                                x86_32::push_r(code, R::EAX);
                                push_value(in.args.at(1));
                                push_value(in.args.at(0));
                            });
                            x86_32::test_r_r(code, R::EAX, R::EAX);
                            trap_if(Cc::NE, "LITHON-E0303: integer overflow in mul -- use wrap_mul() to wrap instead\n");
                            if (try_fast) x86_32::patch_rel32(code, done, code.size());
                            break;
                        }
                        const int32_t al = vslot(in.args.at(0)), bl = vslot(in.args.at(1));
                        x86_32::mov_r_m(code, R::EAX, al);
                        x86_32::mul_m(code, bl);
                        x86_32::mov_m_r(code, vslot(in.result), R::EAX);
                        x86_32::mov_r_r(code, R::ECX, R::EDX);
                        x86_32::mov_r_m(code, R::EAX, al + 4);
                        x86_32::imul_r_m(code, R::EAX, bl);
                        x86_32::alu_r_r(code, Alu::Add, R::ECX, R::EAX);
                        x86_32::mov_r_m(code, R::EAX, al);
                        x86_32::imul_r_m(code, R::EAX, bl + 4);
                        x86_32::alu_r_r(code, Alu::Add, R::ECX, R::EAX);
                        x86_32::mov_m_r(code, vslot(in.result) + 4, R::ECX);
                        break;
                    }
                    if (in.op == Op::Mod) {
                        test_nonzero(in.args.at(1));
                        trap_if(Cc::E, "error: modulo by zero\n");
                        // Fast path: both operands are sign-extended 32-bit values and the
                        // divisor is not -1 (INT_MIN / -1 would fault): one idiv.
                        const ValueId ma = in.args.at(0), mb = in.args.at(1);
                        std::vector<size_t> slow;
                        size_t done = 0;
                        const bool try_fast = (!is_imm(ma) || fits32(ma)) && (!is_imm(mb) || (fits32(mb) && const_val.at(mb) != -1));
                        if (try_fast) {
                            check_fits32_into(R::ECX, mb, slow);
                            if (!is_imm(mb)) {
                                x86_32::alu_r_imm(code, Alu::Cmp, R::ECX, 0xFFFFFFFFu);
                                slow.push_back(x86_32::jcc_rel32(code, Cc::E));
                            }
                            check_fits32_into(R::EAX, ma, slow);
                            x86_32::cdq(code);
                            x86_32::idiv_r(code, R::ECX);
                            x86_32::mov_r_r(code, R::EAX, R::EDX);
                            x86_32::cdq(code);
                            store_pair(in.result);
                            done = x86_32::jmp_rel32(code);
                            for (size_t at : slow) x86_32::patch_rel32(code, at, code.size());
                        }
                        host_call(reinterpret_cast<const void*>(&host32_mod64), 16, [&] {
                            push_value(in.args.at(1));
                            push_value(in.args.at(0));
                        });
                        store_pair(in.result);
                        if (try_fast) x86_32::patch_rel32(code, done, code.size());
                        break;
                    }
                    const bool is_add = in.op == Op::Add || in.op == Op::WrapAdd;
                    const bool checked = in.op == Op::Add || in.op == Op::Sub;
                    take_a(in.args.at(0));
                    alu_lo(is_add ? Alu::Add : Alu::Sub, R::EAX, in.args.at(1));
                    alu_hi(is_add ? Alu::Adc : Alu::Sbb, R::EDX, in.args.at(1));
                    if (checked) {
                        trap_if(Cc::O, is_add
                                 ? "LITHON-E0303: integer overflow in add -- use wrap_add() to wrap instead\n"
                                 : "LITHON-E0303: integer overflow in sub -- use wrap_sub() to wrap instead\n");
                    }
                    finish_pair(in.result);
                    break;
                }
                case Op::Lt: case Op::Gt: case Op::Eq: case Op::Ne:
                    if (is_float(in.args.at(0)) || is_float(in.args.at(1))) {
                        // ucomisd sets CF/ZF/PF; an unordered pair (NaN) sets all three, so
                        // every NaN comparison must come out false except !=. `a < b` is
                        // `b > a`, and "above" (CF=0 and ZF=0) is already false when unordered.
                        const x86_32::Mem a = as_double(in.args.at(0), 0);
                        const x86_32::Mem b = as_double(in.args.at(1), 1);
                        if (in.op == Op::Lt || in.op == Op::Gt) {
                            x86_32::movsd_x_mem(code, 0, in.op == Op::Lt ? b : a);
                            x86_32::ucomisd_x_mem(code, 0, in.op == Op::Lt ? a : b);
                            x86_32::setcc_r8(code, Cc::A, R::EAX);
                            x86_32::movzx_r_r8(code, R::EAX, R::EAX);
                        } else {
                            x86_32::movsd_x_mem(code, 0, a);
                            x86_32::ucomisd_x_mem(code, 0, b);
                            // ==: equal AND ordered.   !=: not equal OR unordered.
                            x86_32::setcc_r8(code, in.op == Op::Eq ? Cc::E : Cc::NE, R::EAX);
                            x86_32::setcc_r8(code, in.op == Op::Eq ? Cc::NP : Cc::P, R::ECX);
                            x86_32::movzx_r_r8(code, R::EAX, R::EAX);
                            x86_32::movzx_r_r8(code, R::ECX, R::ECX);
                            x86_32::alu_r_r(code, in.op == Op::Eq ? Alu::And : Alu::Or, R::EAX, R::ECX);
                        }
                        store_flag(in.result);
                        break;
                    }
                    {
                        // Integer compare. The flags are computed from the pair; if the only
                        // user is the next Branch they are left for it (fusion), else
                        // materialised as a 0/1.
                        Cc cc;
                        take_a(in.args.at(0));
                        const ValueId b = in.args.at(1);
                        if (in.op == Op::Lt) {                 // a < b : cmp alo,blo ; sbb ahi,bhi
                            alu_lo(Alu::Cmp, R::EAX, b);
                            alu_hi(Alu::Sbb, R::EDX, b);
                            cc = Cc::L;
                        } else if (in.op == Op::Gt) {          // a > b : b < a
                            ld_lo(R::ECX, b);
                            x86_32::alu_r_r(code, Alu::Cmp, R::ECX, R::EAX);
                            ld_hi(R::ECX, b);
                            x86_32::alu_r_r(code, Alu::Sbb, R::ECX, R::EDX);
                            cc = Cc::L;
                        } else {                               // == / != : (alo^blo) | (ahi^bhi)
                            alu_lo(Alu::Xor, R::EAX, b);
                            alu_hi(Alu::Xor, R::EDX, b);
                            x86_32::alu_r_r(code, Alu::Or, R::EAX, R::EDX);
                            cc = in.op == Op::Eq ? Cc::E : Cc::NE;
                        }
                        const auto uc = use_count.find(in.result);
                        const bool fuse = uc != use_count.end() && uc->second == 1 &&
                                          pos + 1 < block.instrs.size() &&
                                          block.instrs[pos + 1].op == Op::Branch &&
                                          block.instrs[pos + 1].args.size() == 1 &&
                                          block.instrs[pos + 1].args[0] == in.result;
                        if (fuse) {
                            fused_flag = in.result;
                            fused_cc = cc;
                        } else {
                            x86_32::setcc_r8(code, cc, R::EAX);
                            x86_32::movzx_r_r8(code, R::EAX, R::EAX);
                            store_flag(in.result);
                        }
                    }
                    break;
                case Op::And: case Op::Or: {
                    // Value semantics, as in Python: `x and y` is y when x is truthy,
                    // else x; `x or y` is x when x is truthy, else y.
                    if (is_float(in.args.at(0)) || is_float(in.args.at(1))) unsupported("and/or on floats");
                    test_nonzero(in.args.at(0));
                    const ValueId pick_if_true = in.op == Op::And ? in.args.at(1) : in.args.at(0);
                    const ValueId pick_if_false = in.op == Op::And ? in.args.at(0) : in.args.at(1);
                    const size_t to_false = x86_32::jcc_rel32(code, Cc::E);
                    load_pair(pick_if_true, R::EAX, R::EDX);
                    const size_t done = x86_32::jmp_rel32(code);
                    x86_32::patch_rel32(code, to_false, code.size());
                    load_pair(pick_if_false, R::EAX, R::EDX);
                    x86_32::patch_rel32(code, done, code.size());
                    store_pair(in.result);
                    break;
                }
                case Op::Not:
                    if (is_float(in.args.at(0))) unsupported("not on a float");
                    test_nonzero(in.args.at(0));
                    x86_32::setcc_r8(code, Cc::E, R::EAX);
                    x86_32::movzx_r_r8(code, R::EAX, R::EAX);
                    store_flag(in.result);
                    break;
                case Op::BitAnd: case Op::BitOr: case Op::BitXor: {
                    const Alu op = in.op == Op::BitAnd ? Alu::And : in.op == Op::BitOr ? Alu::Or : Alu::Xor;
                    take_a(in.args.at(0));
                    alu_lo(op, R::EAX, in.args.at(1));
                    alu_hi(op, R::EDX, in.args.at(1));
                    finish_pair(in.result);
                    break;
                }
                case Op::Shl: case Op::Shr: {
                    const bool is_shl = in.op == Op::Shl;
                    const char* const shift_msg = is_shl ? "error: shift count out of range 0..63 for `<<`\n"
                                                         : "error: shift count out of range 0..63 for `>>`\n";
                    const ValueId cnt = in.args.at(1);
                    if (is_imm(cnt)) {
                        // A constant count: an inline double-width shift of EDX:EAX.
                        const int64_t c = const_val.at(cnt);
                        if (c < 0 || c > 63) { trap(shift_msg); break; }
                        take_a(in.args.at(0));
                        const uint8_t n = static_cast<uint8_t>(c);
                        if (is_shl) {
                            if (n == 0) {}
                            else if (n < 32) { x86_32::shld_r_r_imm8(code, R::EDX, R::EAX, n); x86_32::shl_r_imm8(code, R::EAX, n); }
                            else { x86_32::mov_r_r(code, R::EDX, R::EAX); if (n > 32) x86_32::shl_r_imm8(code, R::EDX, n - 32); x86_32::xor_zero(code, R::EAX); }
                        } else {
                            if (n == 0) {}
                            else if (n < 32) { x86_32::shrd_r_r_imm8(code, R::EAX, R::EDX, n); x86_32::sar_r_imm8(code, R::EDX, n); }
                            else { x86_32::mov_r_r(code, R::EAX, R::EDX); if (n > 32) x86_32::sar_r_imm8(code, R::EAX, n - 32); x86_32::sar_r_imm8(code, R::EDX, 31); }
                        }
                        finish_pair(in.result);
                        break;
                    }
                    // A variable count must be 0..63: high dword zero and low dword <= 63.
                    ld_hi(R::EAX, cnt);
                    x86_32::test_r_r(code, R::EAX, R::EAX);
                    trap_if(Cc::NE, shift_msg);
                    ld_lo(R::EAX, cnt);
                    x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, 63);
                    trap_if(Cc::A, shift_msg);
                    host_call(reinterpret_cast<const void*>(is_shl ? &host32_shl64 : &host32_sar64), 12, [&] {
                        x86_32::push_m(code, vslot(cnt));
                        push_value(in.args.at(0));
                    });
                    store_pair(in.result);
                    break;
                }
                case Op::ToFloat: {
                    if (is_float(in.args.at(0))) {
                        load_pair(in.args.at(0), R::EAX, R::EDX);
                        store_pair(in.result);
                    } else {
                        host_call(reinterpret_cast<const void*>(&host32_i2d), 12, [&] {
                            x86_32::lea_r_m(code, R::EAX, vslot(in.result));
                            x86_32::push_r(code, R::EAX);
                            push_value(in.args.at(0));
                        });
                    }
                    break;
                }
                case Op::ToInt: {
                    if (in.type_width < 8 || in.type_width > 64) unsupported("an int conversion of this width");
                    if (!is_float(in.args.at(0))) unsupported("int() of a non-float");
                    host_call(reinterpret_cast<const void*>(&host32_f2i), 16, [&] {
                        x86_32::lea_r_m(code, R::EAX, vslot(in.result));
                        x86_32::push_r(code, R::EAX);
                        x86_32::push_imm32(code, static_cast<uint32_t>(in.type_width));
                        push_value(in.args.at(0));
                    });
                    x86_32::test_r_r(code, R::EAX, R::EAX);
                    trap_if(Cc::NE, kE0309Message);
                    break;
                }
                case Op::GuardNonZero: {
                    test_nonzero(in.args.at(0));
                    trap_if(Cc::E, kE0310Message);
                    break;
                }
                case Op::UTrap: {
                    test_nonzero(in.args.at(0));
                    trap_if(Cc::NE, kE0313Message);
                    break;
                }
                case Op::Branch: {
                    const auto targets = branch_targets(in);
                    if (targets.size() != 2) throw std::runtime_error("compile_module_x86_32: malformed branch");
                    if (is_float(in.args.at(0))) unsupported("a float used as a condition");
                    Cc cc;
                    if (fused_flag == in.args.at(0)) {
                        cc = fused_cc;
                        fused_flag = kInvalidValue;
                    } else {
                        test_nonzero(in.args.at(0));
                        cc = Cc::NE;
                    }
                    const Cc inv = static_cast<Cc>(static_cast<uint8_t>(cc) ^ 1);
                    if (targets[0] == targets[1]) {
                        if (targets[0] != next_label) jump_patches.push_back({x86_32::jmp_rel32(code), targets[0]});
                    } else if (targets[0] == next_label) {
                        jump_patches.push_back({x86_32::jcc_rel32(code, inv), targets[1]});
                    } else if (targets[1] == next_label) {
                        jump_patches.push_back({x86_32::jcc_rel32(code, cc), targets[0]});
                    } else {
                        jump_patches.push_back({x86_32::jcc_rel32(code, inv), targets[1]});
                        jump_patches.push_back({x86_32::jmp_rel32(code), targets[0]});
                    }
                    break;
                }
                case Op::Jump: {
                    const auto targets = branch_targets(in);
                    if (targets.empty()) throw std::runtime_error("compile_module_x86_32: malformed jump");
                    if (targets[0] != next_label) jump_patches.push_back({x86_32::jmp_rel32(code), targets[0]});
                    break;
                }
                case Op::Return:
                    if (!in.args.empty()) take_a(in.args.at(0));
                    emit_return_tail();
                    break;
                case Op::Call: {
                    const std::string& name = in.name;
                    if (name == "print" || name == "print_bare" || name == "print_u" || name == "print_bare_u") {
                        if (in.args.size() != 1) unsupported("print() with other than one argument");
                        const bool nl = name == "print" || name == "print_u";
                        const bool uns = name == "print_u" || name == "print_bare_u";
                        const ValueId arg = in.args.at(0);
                        const Kind k = arg < kinds.size() ? kinds[arg] : Kind::Unknown;
                        if (k == Kind::Int) {
                            const char* fmt = uns ? (nl ? kFmtUInt : kFmtUIntBare) : (nl ? kFmtInt : kFmtIntBare);
                            host_call(reinterpret_cast<const void*>(&std::printf), 12, [&] {
                                push_value(arg);
                                x86_32::push_imm32(code, addr_of(fmt));
                            });
                        } else if (k == Kind::Bool) {
                            const char* t = nl ? kTrue : kTrueBare;
                            const char* f = nl ? kFalse : kFalseBare;
                            const int pad = (kStackAlign - 4 % kStackAlign) % kStackAlign;
                            x86_32::sub_esp(code, pad);
                            x86_32::mov_r_imm(code, R::EAX, addr_of(f));
                            ld_lo(R::EDX, arg);
                            alu_hi(Alu::Or, R::EDX, arg);
                            const size_t skip = x86_32::jcc_rel32(code, Cc::E);
                            x86_32::mov_r_imm(code, R::EAX, addr_of(t));
                            x86_32::patch_rel32(code, skip, code.size());
                            x86_32::push_r(code, R::EAX);
                            align_probe();
                            x86_32::mov_r_imm(code, R::EAX, addr_of(reinterpret_cast<const void*>(&std::printf)));
                            x86_32::call_r(code, R::EAX);
                            x86_32::add_esp(code, 4 + pad);
                        } else if (k == Kind::Float) {
                            // Format in the host (CPython's repr rules, float_runtime.h), then
                            // print the resulting string with "%s".
                            host_call(reinterpret_cast<const void*>(&host_format_double), 8,
                                      [&] { push_value(arg); });
                            host_call(reinterpret_cast<const void*>(&std::printf), 8, [&] {
                                x86_32::push_r(code, R::EAX);
                                x86_32::push_imm32(code, addr_of(nl ? kFmtStr : kFmtStrBare));
                            });
                        } else {
                            unsupported("print of a value that is not int, bool or float");
                        }
                        break;
                    }
                    if (name == "print_sep" || name == "print_nl") {
                        const char* lit = name == "print_nl" ? kNl : kSep;
                        host_call(reinterpret_cast<const void*>(&std::printf), 4,
                                  [&] { x86_32::push_imm32(code, addr_of(lit)); });
                        break;
                    }
                    // A user function. cdecl, each argument a pair.
                    const Function* callee = nullptr;
                    for (const auto& mf : module.functions) if (mf.name == name) { callee = &mf; break; }
                    if (!callee) unsupported(("the builtin '" + name + "'").c_str());
                    if (callee->params.size() != in.args.size())
                        throw std::runtime_error("compile_module_x86_32: call to '" + name + "' with " +
                                                 std::to_string(in.args.size()) + " arguments but it has " +
                                                 std::to_string(callee->params.size()) + " parameters");
                    const int bytes = 8 * static_cast<int>(in.args.size());
                    const int pad = (kStackAlign - bytes % kStackAlign) % kStackAlign;
                    x86_32::sub_esp(code, pad);
                    for (size_t a = in.args.size(); a-- > 0;) push_value(in.args[a]);
                    align_probe();
                    call_patches.push_back({x86_32::call_rel32(code), name});
                    x86_32::add_esp(code, bytes + pad);
                    if (in.result != kInvalidValue) store_pair(in.result);
                    break;
                }
                case Op::Len: {
                    const int cap = alloc.container_capacity(in.name);
                    if (cap <= 0) throw std::runtime_error("compile_module_x86_32: len() of '" + in.name + "' which is not a container");
                    x86_32::mov_m_imm(code, vslot(in.result), static_cast<uint32_t>(cap));
                    x86_32::mov_m_imm(code, vslot(in.result) + 4, 0);
                    break;
                }
                case Op::Index: case Op::IndexStore: {
                    const bool is_store = in.op == Op::IndexStore;
                    const bool flat = alloc.has_flat_layout(in.name);
                    const int32_t disp = alloc.element_offset(in.name, 0);
                    int bytes;
                    size_t value_pos;
                    if (flat) {
                        const auto* lay = alloc.flat_layout(in.name);
                        const auto type = alloc.container_type(in.name);
                        const size_t n_idx = is_store ? in.args.size() - 1 : in.args.size();
                        flat_index_to_ecx(in, *lay, *type, n_idx);
                        bytes = lay->leaf_bytes;
                        value_pos = n_idx;
                    } else {
                        const int cap = alloc.container_capacity(in.name);
                        if (cap <= 0) throw std::runtime_error("compile_module_x86_32: index into '" + in.name + "' which is not a container");
                        idx_checked_to(R::ECX, in.args.at(0), cap);
                        bytes = alloc.container_stride(in.name);
                        value_pos = 1;
                    }
                    const x86_32::Mem m = x86_32::at_ebp_idx(disp, R::ECX, static_cast<uint8_t>(bytes));
                    if (is_store) {
                        mem_store(m, in.args.at(value_pos), bytes);
                    } else {
                        const Kind rk = in.result < kinds.size() ? kinds[in.result] : Kind::Unknown;
                        mem_load(in.result, bytes, rk != Kind::Bool && rk != Kind::Object, m);
                        if (rk == Kind::Object) {
                            // A class container is fully initialised by its literal, so a null
                            // leaf was never written: trap like the interpreter.
                            x86_32::test_r_r(code, R::EAX, R::EAX);
                            trap_if(Cc::E, "error: null class reference\n");
                        }
                    }
                    break;
                }
                case Op::FieldLoad: case Op::FieldStore: {
                    const bool is_store = in.op == Op::FieldStore;
                    const bool by_ptr = in.name.empty();
                    int bytes = in.type_kind == "bool" ? 1 : in.type_kind == "float" ? 8
                              : in.type_kind == "class" ? 8 : in.type_width / 8;
                    if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8) unsupported("a field of this width");
                    x86_32::Mem m;
                    if (by_ptr) {
                        const ValueId ptr = is_store ? in.args.at(1) : in.args.at(0);
                        x86_32::mov_r_m(code, R::ECX, vslot(ptr));
                        m = x86_32::at_reg(R::ECX, static_cast<int32_t>(in.int_imm));
                    } else {
                        if (alloc.struct_size(in.name) == 0)
                            throw std::runtime_error("compile_module_x86_32: field access on '" + in.name + "' which is not a struct");
                        m = x86_32::at_ebp(voff(in.name) + static_cast<int32_t>(in.int_imm));
                    }
                    if (is_store) mem_store(m, in.args.at(0), bytes);
                    else mem_load(in.result, bytes, in.type_kind != "bool" && in.type_kind != "class", m);
                    break;
                }
                case Op::AddressOf:
                    x86_32::lea_r_m(code, R::EAX, voff(in.name));
                    x86_32::xor_zero(code, R::EDX);
                    store_pair(in.result);
                    break;
                case Op::ValueOf: {
                    x86_32::mov_r_m(code, R::ECX, vslot(in.args.at(0)));
                    int bytes = 8;
                    if (in.type_kind == "bool") bytes = 1;
                    else if (in.type_kind == "int" && in.type_width > 0) bytes = in.type_width / 8;
                    mem_load(in.result, bytes, in.type_kind != "bool", x86_32::at_reg(R::ECX, 0));
                    break;
                }
                case Op::StrLen: {
                    const int cap = alloc.container_capacity(in.name);
                    if (cap <= 0) throw std::runtime_error("compile_module_x86_32: strlen of '" + in.name + "' which is not a str");
                    ContainerStrLenDesc desc{};
                    desc.cap = cap;
                    call_desc(reinterpret_cast<const void*>(&host_strlen), alloc.element_offset(in.name, 0),
                              register_print_desc(desc));
                    store_pair(in.result);
                    break;
                }
                case Op::StrEq: case Op::StrNe: {
                    const int cap1 = alloc.container_capacity(in.name), cap2 = alloc.container_capacity(in.name2);
                    if (cap1 <= 0 || cap2 <= 0) throw std::runtime_error("compile_module_x86_32: str compare of non-strs");
                    ContainerStrEqDesc desc{};
                    desc.base1 = alloc.element_offset(in.name, 0); desc.cap1 = cap1;
                    desc.base2 = alloc.element_offset(in.name2, 0); desc.cap2 = cap2;
                    call_desc(reinterpret_cast<const void*>(&host_streq), 0, register_print_desc(desc));
                    if (in.op == Op::StrNe) x86_32::alu_r_imm(code, Alu::Xor, R::EAX, 1);
                    store_pair(in.result);
                    break;
                }
                case Op::StrFind: case Op::StrCount: {
                    const int cap1 = alloc.container_capacity(in.name), cap2 = alloc.container_capacity(in.name2);
                    if (cap1 <= 0 || cap2 <= 0) throw std::runtime_error("compile_module_x86_32: str search of non-strs");
                    ContainerStrFindDesc desc{};
                    desc.base1 = alloc.element_offset(in.name, 0); desc.cap1 = cap1;
                    desc.base2 = alloc.element_offset(in.name2, 0); desc.cap2 = cap2;
                    call_desc(reinterpret_cast<const void*>(in.op == Op::StrFind ? &host_strfind : &host_strcount),
                              0, register_print_desc(desc));
                    store_pair(in.result);
                    break;
                }
                case Op::StrSplit: {
                    const int src_cap = alloc.container_capacity(in.name), sep_cap = alloc.container_capacity(in.name2);
                    if (src_cap <= 0 || sep_cap <= 0) throw std::runtime_error("compile_module_x86_32: strsplit of non-strs");
                    StrSplitDesc desc{};
                    desc.src_offset = alloc.element_offset(in.name, 0); desc.src_cap = src_cap;
                    desc.sep_offset = alloc.element_offset(in.name2, 0); desc.sep_cap = sep_cap;
                    desc.dest_offset = alloc.element_offset(in.name3, 0);
                    desc.piece_cap = in.type_elem_width; desc.max_pieces = in.type_width;
                    call_desc(reinterpret_cast<const void*>(&host_strsplit), 0, register_print_desc(desc));
                    x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, static_cast<uint32_t>(in.type_width));
                    const size_t fits = x86_32::jcc_rel32(code, Cc::LE);
                    trap("error: too many pieces for the list (str.split)\n");
                    x86_32::patch_rel32(code, fits, code.size());
                    store_pair(in.result);
                    break;
                }
                case Op::InputInt: {
                    // The prompt must outlive the code: keep every one in a stable pool.
                    static std::vector<std::unique_ptr<std::string>> prompts;
                    prompts.push_back(std::make_unique<std::string>(in.name));
                    const char* prompt = prompts.back()->c_str();
                    const char* const msgs[3] = {kE0306Message, kE0307Message, kE0308Message};
                    const char* const names[3] = {"ValueError", "OverflowError", "EOFError"};
                    bool any_catchable = false;
                    for (const char* n : names) if (!handler_label(n).empty()) any_catchable = true;
                    (void)any_catchable;   // always the error-code path: a failed read traps at this site, with its position
                    host_call(reinterpret_cast<const void*>(&host_input_int_ex), 16, [&] {
                        x86_32::lea_r_m(code, R::EAX, scratch(0));
                        x86_32::push_r(code, R::EAX);
                        x86_32::push_imm32(code, 0);
                        x86_32::push_imm32(code, static_cast<uint32_t>(in.type_width));
                        x86_32::push_imm32(code, addr_of(prompt));
                    });
                    x86_32::test_r_r(code, R::EAX, R::EAX);
                    const size_t ok = x86_32::jcc_rel32(code, Cc::E);
                    for (int i = 0; i < 3; ++i) {
                        x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, static_cast<uint32_t>(i + 1));
                        const size_t skip = x86_32::jcc_rel32(code, Cc::NE);
                        trap(msgs[i]);
                        x86_32::patch_rel32(code, skip, code.size());
                    }
                    trap(kE0306Message);
                    x86_32::patch_rel32(code, ok, code.size());
                    x86_32::mov_r_m(code, R::EAX, scratch(0));
                    x86_32::mov_r_m(code, R::EDX, scratch(0) + 4);
                    store_pair(in.result);
                    break;
                }
                case Op::InputStr: {
                    static std::vector<std::unique_ptr<std::string>> prompts;
                    prompts.push_back(std::make_unique<std::string>(in.name2));
                    const char* prompt = prompts.back()->c_str();
                    const int dest_cap = alloc.container_capacity(in.name);
                    if (dest_cap <= 0) throw std::runtime_error("compile_module_x86_32: inputstr into '" + in.name + "' which is not a str");
                    StrInputDesc desc{};
                    desc.dest_offset = alloc.element_offset(in.name, 0);
                    desc.cap = dest_cap;
                    desc.limit = in.int_imm;
                    host_call(reinterpret_cast<const void*>(&host_input_str), 12, [&] {
                        x86_32::push_imm32(code, addr_of(prompt));
                        x86_32::push_imm32(code, addr_of(register_print_desc(desc)));
                        x86_32::push_r(code, R::EBP);
                    });
                    x86_32::test_r_r(code, R::EAX, R::EAX);
                    const size_t ok = x86_32::jcc_rel32(code, Cc::E);
                    x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, 1);
                    const size_t not_long = x86_32::jcc_rel32(code, Cc::NE);
                    trap(kE0311Message);
                    x86_32::patch_rel32(code, not_long, code.size());
                    x86_32::alu_r_imm(code, Alu::Cmp, R::EAX, 2);
                    const size_t not_nul = x86_32::jcc_rel32(code, Cc::NE);
                    trap(kE0312Message);
                    x86_32::patch_rel32(code, not_nul, code.size());
                    trap(kE0308Message);
                    x86_32::patch_rel32(code, ok, code.size());
                    break;
                }
                case Op::Syscall: {
                    const size_t nargs = in.args.size();
                    if (nargs == 0 || nargs > 7) throw std::runtime_error("compile_module_x86_32: syscall takes a number and at most six arguments");
                    for (int pos = 6; pos >= 1; --pos) {
                        if (static_cast<size_t>(pos) < nargs) push_value(in.args[static_cast<size_t>(pos)]);
                        else { x86_32::push_imm32(code, 0); x86_32::push_imm32(code, 0); }
                    }
                    x86_32::mov_r_r(code, R::EAX, R::ESP);   // &args[0], before any padding
                    host_call(reinterpret_cast<const void*>(&host_syscall), 12, [&] {
                        x86_32::push_r(code, R::EAX);
                        push_value(in.args[0]);
                    });
                    x86_32::add_esp(code, 48);
                    if (in.result != kInvalidValue) store_pair(in.result);
                    break;
                }
                case Op::PrintContainer: {
                    emit_print_container(in);
                    break;
                }
                case Op::DictStore: case Op::DictIndex: case Op::DictContains: {
                    const auto& d = alloc.dict_layout(in.name);
                    if (d.buckets <= 0) throw std::runtime_error("compile_module_x86_32: '" + in.name + "' is not a dict");
                    const bool is_str = d.key_kind == "str";
                    const bool container_value = d.value_kind == "list" || d.value_kind == "tuple";
                    static std::vector<std::unique_ptr<Dict32Desc>> dict_descs;
                    dict_descs.push_back(std::make_unique<Dict32Desc>());
                    Dict32Desc& dd = *dict_descs.back();
                    dd = Dict32Desc{};
                    dd.buckets = d.buckets; dd.keys_offset = d.keys_offset; dd.occupied_offset = d.occupied_offset;
                    dd.values_offset = d.values_offset; dd.key_stride = d.key_stride; dd.value_stride = d.value_stride;
                    dd.key_is_str = is_str; dd.key_is_bool = d.key_kind == "bool"; dd.key_width = d.key_width;
                    dd.value_is_signed_int = d.value_kind == "int";
                    dd.value_is_container = container_value;
                    if (is_str) {
                        const int cap = alloc.container_capacity(in.name2);
                        if (cap <= 0) throw std::runtime_error("compile_module_x86_32: str key '" + in.name2 + "' which is not a str");
                        if (cap > d.key_stride)
                            throw std::runtime_error("compile_module_x86_32: str key '" + in.name2 + "' exceeds dict '" + in.name + "' key capacity");
                        dd.str = DictStrDesc{d.buckets, d.keys_offset, d.occupied_offset, d.key_stride,
                                             alloc.element_offset(in.name2, 0), cap};
                    }
                    auto push_key = [&]() {   // the key as an int64 (0 for a str key: the run is the key)
                        if (is_str) { x86_32::push_imm32(code, 0); x86_32::push_imm32(code, 0); }
                        else push_value(in.args.at(0));
                    };
                    if (in.op == Op::DictStore) {
                        const ValueId vid = container_value ? kInvalidValue : is_str ? in.args.at(0) : in.args.at(1);
                        host_call(reinterpret_cast<const void*>(&host32_dict_store), 20, [&] {
                            if (container_value) x86_32::lea_r_m(code, R::EAX, alloc.element_offset(in.name3, 0));
                            else x86_32::lea_r_m(code, R::EAX, vslot(vid));
                            x86_32::push_r(code, R::EAX);
                            push_key();
                            x86_32::push_imm32(code, addr_of(&dd));
                            x86_32::push_r(code, R::EBP);
                        });
                        break;
                    }
                    const bool is_contains = in.op == Op::DictContains;
                    host_call(reinterpret_cast<const void*>(&host32_dict_get), 20, [&] {
                        if (is_contains) x86_32::push_imm32(code, 0);
                        else if (container_value) {
                            x86_32::lea_r_m(code, R::EAX, alloc.element_offset(in.name3, 0));
                            x86_32::push_r(code, R::EAX);
                        } else {
                            x86_32::lea_r_m(code, R::EAX, vslot(in.result));
                            x86_32::push_r(code, R::EAX);
                        }
                        push_key();
                        x86_32::push_imm32(code, addr_of(&dd));
                        x86_32::push_r(code, R::EBP);
                    });
                    if (is_contains) {
                        x86_32::xor_zero(code, R::EDX);
                        store_pair(in.result);
                    } else {
                        x86_32::test_r_r(code, R::EAX, R::EAX);
                        trap_if(Cc::E, "error: dict key not found\n");
                    }
                    break;
                }
                case Op::New: {
                    const auto it = class_index.find(in.name);
                    if (it == class_index.end()) throw std::runtime_error("compile_module_x86_32: new of unknown class '" + in.name + "'");
                    host_call(reinterpret_cast<const void*>(&runtime::host_class_new), 8, [&] {
                        x86_32::push_imm32(code, 0);
                        x86_32::push_imm32(code, static_cast<uint32_t>(it->second));
                    });
                    x86_32::xor_zero(code, R::EDX);
                    store_pair(in.result);
                    break;
                }
                case Op::Retain:
                    class_ptr_call(reinterpret_cast<const void*>(&runtime::host_class_retain), in.args.at(0), 0, [] {});
                    x86_32::xor_zero(code, R::EDX);
                    store_pair(in.result);
                    break;
                case Op::Release:
                    class_ptr_call(reinterpret_cast<const void*>(&runtime::host_class_release), in.args.at(0), 0, [] {});
                    break;
                case Op::IsInstance:
                    class_ptr_call(reinterpret_cast<const void*>(&runtime::host_class_is_instance), in.args.at(0), 8, [&] {
                        x86_32::push_imm32(code, static_cast<uint32_t>(static_cast<uint64_t>(in.int_imm) >> 32));
                        x86_32::push_imm32(code, static_cast<uint32_t>(in.int_imm));
                    });
                    x86_32::movzx_r_r8(code, R::EAX, R::EAX);   // a C++ bool comes back in AL only
                    x86_32::xor_zero(code, R::EDX);
                    store_pair(in.result);
                    break;
                case Op::RefLoad: case Op::RefStore: {
                    const bool is_store = in.op == Op::RefStore;
                    const ValueId ptr = in.args.at(0);
                    const int bytes = in.type_kind == "bool" ? 1 : in.type_kind == "class" ? 8
                                    : in.type_kind == "float" ? 8 : in.type_width / 8;
                    if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8) unsupported("an attribute of this width");
                    x86_32::mov_r_m(code, R::ECX, vslot(ptr));
                    const x86_32::Mem m = x86_32::at_reg(R::ECX, static_cast<int32_t>(in.int_imm));
                    if (is_store) mem_store(m, in.args.at(1), bytes);
                    else mem_load(in.result, bytes, in.type_kind != "bool" && in.type_kind != "class", m);
                    break;
                }
                case Op::CallVirtual: case Op::CallSuper: {
                    const size_t nargs = in.args.size();
                    if (nargs < 1) throw std::runtime_error("compile_module_x86_32: virtual call with no receiver");
                    // 1. resolve the target through the runtime (it returns the function address)
                    if (in.op == Op::CallVirtual) {
                        host_call(reinterpret_cast<const void*>(&runtime::host_class_slot), 12, [&] {
                            x86_32::push_imm32(code, static_cast<uint32_t>(static_cast<uint64_t>(in.int_imm) >> 32));
                            x86_32::push_imm32(code, static_cast<uint32_t>(in.int_imm));
                            x86_32::push_m(code, vslot(in.args[0]));
                        });
                    } else {
                        const uint64_t packed = (static_cast<uint64_t>(in.int_imm2) << 32) |
                                                (static_cast<uint64_t>(in.int_imm) & 0xffffffffULL);
                        host_call(reinterpret_cast<const void*>(&runtime::host_class_super_slot), 12, [&] {
                            x86_32::push_imm32(code, static_cast<uint32_t>(packed >> 32));
                            x86_32::push_imm32(code, static_cast<uint32_t>(packed));
                            x86_32::push_m(code, vslot(in.args[0]));
                        });
                    }
                    x86_32::mov_m_r(code, scratch(2), R::EAX);   // the target survives the pushes
                    // 2. an ordinary cdecl call: receiver first, then the method arguments
                    const int bytes = 8 * static_cast<int>(nargs);
                    const int pad = (kStackAlign - bytes % kStackAlign) % kStackAlign;
                    x86_32::sub_esp(code, pad);
                    for (size_t a = nargs; a-- > 0;) push_value(in.args[a]);
                    x86_32::mov_r_m(code, R::EAX, scratch(2));
                    align_probe();
                    x86_32::call_r(code, R::EAX);
                    x86_32::add_esp(code, bytes + pad);
                    if (in.result != kInvalidValue) store_pair(in.result);
                    break;
                }
                case Op::TryEnter: case Op::TryLeave: case Op::TryCatch:
                    break;   // markers only: the regions were precomputed per block
                default:
                    unsupported("this IR operation");
                }
            }
        }
        // Shared out-of-line trap stubs: one per distinct message. They are reached only by
        // jumping, never by falling through (the last block ends in a terminator), and
        // ESP is the frame's aligned value there because no site has pushes outstanding.
        for (const auto& st : stub_sites) {
            const size_t at = code.size();
            for (size_t site : st.second.sites) x86_32::patch_rel32(code, site, at);
            host_call(reinterpret_cast<const void*>(&host_report_error), 12, [&, loc = st.second.loc] {
                x86_32::push_imm32(code, static_cast<uint32_t>(static_cast<uint64_t>(loc) >> 32));
                x86_32::push_imm32(code, static_cast<uint32_t>(loc));
                x86_32::push_imm32(code, addr_of(st.second.message));
            });
        }
        // The runtime calls a destructor as a plain C function taking ONE pointer; the
        // JIT convention takes each argument as an 8-byte pair. A thunk widens it.
        if (fn.name.rfind("__del__", 0) == 0) {
            thunk_pending.push_back(fn.name);
        }
        for (const auto& jp : jump_patches) {
            const auto it = block_offset.find(jp.label);
            if (it == block_offset.end())
                throw std::runtime_error("compile_module_x86_32: jump to unknown block '" + jp.label + "'");
            x86_32::patch_rel32(code, jp.at, it->second);
        }
    }

    for (const std::string& name : thunk_pending) {
        function_offset[name + "$c"] = code.size();
        // mov eax, [esp+4] ; push 0 ; push eax ; call fn ; add esp, 8 ; ret
        // A C caller leaves ESP = 12 (mod 16) on entry (return address pushed on an aligned
        // stack); two pushes make it 4 (mod 16), so System V needs 4 bytes of padding for
        // the call to be 16-byte aligned.
        const int thunk_pad = kStackAlign == 16 ? 4 : 0;
        for (uint8_t b : {0x8B, 0x44, 0x24, 0x04}) code.push_back(b);
        x86_32::sub_esp(code, thunk_pad);
        x86_32::push_imm32(code, 0);
        x86_32::push_r(code, R::EAX);
        call_patches.push_back({x86_32::call_rel32(code), name});
        x86_32::add_esp(code, 8 + thunk_pad);
        x86_32::ret(code);
    }
    if (export_thunks) {
        auto scalar_ok = [](const std::string& k) { return k == "int" || k == "bool" || k == "float"; };
        for (const Function& fn : module.functions) {
            if (fn.name.rfind("__", 0) == 0) continue;   // internal: __main__, __del__*, __virt__*
            bool ok = scalar_ok(fn.return_type_kind) || fn.return_type_kind.empty();
            for (size_t i = 0; i < fn.params.size(); ++i)
                ok = ok && i < fn.param_type_kinds.size() && scalar_ok(fn.param_type_kinds[i]);
            if (!ok) continue;
            const int n = static_cast<int>(fn.params.size());
            const bool float_ret = fn.return_type_kind == "float";
            auto emit_thunk = [&](int conv /*0 cdecl, 1 stdcall, 2 fastcall*/, const char* suffix) {
                const int reg_params = conv == 2 ? std::min(n, 2) : 0;
                if (conv == 2) {   // fastcall registers carry int/bool of <= 32 bits
                    for (int i = 0; i < reg_params; ++i) {
                        const std::string& k = fn.param_type_kinds[static_cast<size_t>(i)];
                        const int w = static_cast<size_t>(i) < fn.param_type_widths.size()
                                          ? fn.param_type_widths[static_cast<size_t>(i)] : -1;
                        if (k == "float" || (k == "int" && (w < 0 || w > 32))) return;
                    }
                }
                function_offset[fn.name + suffix] = code.size();
                // ESP is 12 (mod 16) on entry from an aligned C caller (System V); size the
                // padding so that it is 16-byte aligned at the inner call.
                const int pad = kStackAlign == 16 ? (((12 - 8 * n) % 16) + 16) % 16 : 0;
                x86_32::sub_esp(code, pad);
                int pushed_pairs = 0;
                for (int i = n - 1; i >= 0; --i) {
                    if (conv == 2 && i < 2) {
                        const bool is_bool = fn.param_type_kinds[static_cast<size_t>(i)] == "bool";
                        x86_32::mov_r_r(code, R::EAX, i == 1 ? R::EDX : R::ECX);
                        if (is_bool) { x86_32::alu_r_imm(code, Alu::And, R::EAX, 0xFF); x86_32::xor_zero(code, R::EDX); }
                        else x86_32::cdq(code);
                    } else {
                        const int j = conv == 2 ? i - 2 : i;
                        const int32_t off = 4 + 8 * j + pad + 8 * pushed_pairs + 4;   // the high dword
                        // push dword [esp + off], twice: the second push sees ESP 4 lower
                        // and the low dword is 4 below the high one, so the displacement is equal.
                        for (int rep = 0; rep < 2; ++rep) {
                            code.push_back(0xFF); code.push_back(0xB4); code.push_back(0x24);
                            x86_32::u32(code, static_cast<uint32_t>(off));
                        }
                        ++pushed_pairs;
                        continue;
                    }
                    x86_32::push_r(code, R::EDX);
                    x86_32::push_r(code, R::EAX);
                    ++pushed_pairs;
                }
                call_patches.push_back({x86_32::call_rel32(code), fn.name});
                x86_32::add_esp(code, 8 * n + pad);
                if (float_ret) {   // C returns a double in ST0
                    x86_32::push_r(code, R::EDX);
                    x86_32::push_r(code, R::EAX);
                    for (uint8_t b : {0xDD, 0x04, 0x24}) code.push_back(b);   // fld qword [esp]
                    x86_32::add_esp(code, 8);
                }
                const int pop = conv == 0 ? 0 : 8 * (n - reg_params);
                if (pop == 0) x86_32::ret(code);
                else { code.push_back(0xC2); code.push_back(static_cast<uint8_t>(pop)); code.push_back(static_cast<uint8_t>(pop >> 8)); }
            };
            if (float_ret) emit_thunk(0, "$cdecl"); else function_offset[fn.name + "$cdecl"] = function_offset.at(fn.name);
            emit_thunk(1, "$stdcall");
            emit_thunk(2, "$fastcall");
        }
    }
    for (const auto& cp : call_patches) {
        const auto it = function_offset.find(cp.callee);
        if (it == function_offset.end())
            throw std::runtime_error("compile_module_x86_32: call to unknown function '" + cp.callee + "'");
        x86_32::patch_rel32(code, cp.at, it->second);
    }

    CompiledModule out;
    out.code = std::move(code);
    out.function_offset = std::move(function_offset);
    return out;
}

}  // namespace lithon::jit::x86_32
