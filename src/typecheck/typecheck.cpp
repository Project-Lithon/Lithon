#include "typecheck.h"
#include "../dict_hash.h"
#include "../jit/float_runtime.h"
#include <unordered_map>
#include <unordered_set>
#include <cstdint>
#include <algorithm>
#include <limits>

namespace lithon::typecheck {

using namespace lithon::ir;

namespace {

// 4.1. A type is now recursive: `list[int[64], 10]` is a kind, a capacity, and
// an ELEMENT type. Element types are held one level deep rather than as a
// shared_ptr tree, which is enough for list/tuple/ptr (4.2/4.3/4.4) and keeps
// LType cheap to copy in the scope maps.
//
// The element MUST participate in equality. Without it `list[int[64],10]` and
// `list[float[64],10]` would compare equal, and assigning one to the other
// would pass the checker -- which is exactly the class of bug a typed container
// is supposed to make impossible.
struct LType {
    std::string kind;
    int width = -1;
    std::string elem_kind;    // element type for list/tuple/ptr; "" when scalar
    int elem_width = -1;

    // 4.3. A dict has two element types, so the pair above is not enough. The
    // value stays in elem_kind/elem_width and the key is held here, which keeps
    // every existing reader of a dict's values correct without changes.
    std::string key_kind;
    int key_width = -1;

    bool operator==(const LType& other) const {
        return kind == other.kind && width == other.width &&
               elem_kind == other.elem_kind && elem_width == other.elem_width &&
               key_kind == other.key_kind && key_width == other.key_width;
    }
    bool operator!=(const LType& other) const { return !(*this == other); }

    // 4.1. The single place that decides "does this have a bit width?". A
    // container's `width` is a CAPACITY, and int_range() on a capacity would
    // silently produce a bogus range -- int_range(10) is [-512, 511], so a
    // `list[int[64],10]` misread as a 10-bit int would be diagnosed as an
    // int[10] overflow and accepted where it should be rejected (and vice
    // versa). Every caller must go through this.
    //
    // 4.3. A dict's width is a BUCKET COUNT, not a capacity, so it must never
    // reach int_range either. key_kind being set is what keeps it out.
    bool is_scalar() const { return elem_kind.empty() && key_kind.empty(); }
    bool is_list() const { return kind == "list"; }
    bool is_dict() const { return kind == "dict"; }
    bool is_ptr() const { return kind == "ptr"; }

    // 4.4. The element stride a pointer offset is scaled by, in BYTES. The
    // frontend lowers `_p + 1` to `add _p, sizeof(T)`, so an offset is a byte
    // count and this is what both it and the checker multiply/divide by. A
    // bool and an int[8] both pack to one byte.
    int byte_stride() const {
        if (elem_kind == "bool") return 1;
        if (elem_width >= 8) return elem_width / 8;
        return 1;
    }

    // The element type of a container, as a standalone LType.
    LType element() const { return LType{elem_kind, elem_width, "", -1, "", -1}; }
    LType key() const { return LType{key_kind, key_width, "", -1, "", -1}; }
};

std::pair<int64_t, int64_t> int_range(int width) {
    if (width >= 64) {
        return {std::numeric_limits<int64_t>::min(), std::numeric_limits<int64_t>::max()};
    }
    int64_t half = int64_t(1) << (width - 1);
    return {-half, half - 1};
}

// Renders a 128-bit value for an error message. std::to_string has no
// __int128 overload (it is ambiguous between the signed/unsigned/long long
// candidates), and the shift range check below can legitimately hold a value
// outside int64 -- an int[8] shifted by 63 is a 71-bit magnitude.
std::string wide_str(__int128 v) {
    bool neg = v < 0;
    unsigned __int128 m = neg ? (static_cast<unsigned __int128>(-(v + 1)) + 1)
                              : static_cast<unsigned __int128>(v);
    std::string s;
    do { s.insert(s.begin(), static_cast<char>('0' + static_cast<int>(m % 10))); m /= 10; } while (m);
    return neg ? "-" + s : s;
}

std::string type_str(const LType& t) {
    if (t.is_scalar()) {
        if (t.width < 0) return t.kind;
        return t.kind + "[" + std::to_string(t.width) + "]";
    }
    // 4.1. Render a container back the way it was written, so a diagnostic
    // quotes the source spelling rather than a flattened approximation.
    // A nested container element is one level deeper than LType holds, so it is
    // rendered as `list[...]`. Printing its capacity alone (`list[4]`) would
    // read like a list of ints named 4, which is a different type entirely.
    std::string elem = t.elem_kind.empty() ? "?" : t.elem_kind;
    if (t.elem_kind == "list" || t.elem_kind == "tuple" || t.elem_kind == "ptr") {
        return t.kind + "[" + elem + "[...], " + std::to_string(t.width) + "]";
    }
    // 4.3. A dict prints as it was written, key first, because that is the
    // order it was declared in.
    if (t.is_dict()) {
        std::string k = t.key_kind.empty() ? "?" : t.key_kind;
        if (t.key_width >= 0) k += "[" + std::to_string(t.key_width) + "]";
        if (t.elem_width >= 0) elem += "[" + std::to_string(t.elem_width) + "]";
        return t.kind + "[" + k + ", " + elem + ", " + std::to_string(t.width) + "]";
    }
    if (t.elem_width >= 0) elem += "[" + std::to_string(t.elem_width) + "]";
    // ptr carries no capacity and must not print a phantom ", -1".
    if (t.kind == "ptr") return t.kind + "[" + elem + "]";
    return t.kind + "[" + elem + ", " + std::to_string(t.width) + "]";
}

using Scope = std::unordered_map<std::string, LType>;

struct FnMeta {
    std::vector<LType> param_types;
    LType return_type;
    bool has_return_type = false;
};

class FunctionChecker {
public:
    FunctionChecker(const Module& module, const Function& fn, std::vector<RCRError>& errors,
                     const std::unordered_map<std::string, FnMeta>& all_fns)
        : module_(module), fn_(fn), errors_(errors), all_fns_(all_fns) {}

    void run() {
        bool has_return_type = !fn_.return_type_kind.empty();
        if (has_return_type) {
            return_type_ = LType{fn_.return_type_kind, fn_.return_type_width};
            check_scalar_width(fn_.return_type_kind, fn_.return_type_width);
        }
        has_return_type_ = has_return_type;

        Scope entry_scope;
        for (size_t i = 0; i < fn_.params.size(); ++i) {
            if (fn_.param_type_kinds[i].empty()) {
                error("function '" + fn_.name + "': parameter '" + fn_.params[i] +
                      "' has no type annotation (V1_SPEC 0.6.1, 0.6.8)");
                continue;
            }
            entry_scope[fn_.params[i]] = LType{fn_.param_type_kinds[i], fn_.param_type_widths[i]};
            check_scalar_width(fn_.param_type_kinds[i], fn_.param_type_widths[i]);
        }
        // Entry points are exempt from the return-annotation rule: the frontend's
        // implicit top-level function is __main__, and hand-written IR may use main.
        if (fn_.name != "main" && fn_.name != "__main__" && !has_return_type) {
            error("function '" + fn_.name + "' has no return type annotation (V1_SPEC 0.6.8)");
        }

        build_block_graph();
        exempt_increment_stores_ = find_loop_increment_stores();
        walk_blocks(entry_scope);
        check_for_loop_shapes();
    }

private:
    const Module& module_;
    const Function& fn_;
    std::vector<RCRError>& errors_;
    const std::unordered_map<std::string, FnMeta>& all_fns_;
    std::unordered_map<ValueId, LType> reg_types_;
    // 4.2: names currently accepting their own initializer stores (see walk_blocks).
    std::unordered_set<std::string> tuple_open_for_init_;

    LType return_type_;
    bool has_return_type_ = false;

    std::unordered_map<std::string, size_t> block_index_;
    std::unordered_map<std::string, std::vector<std::string>> predecessors_;
    std::unordered_map<std::string, Scope> out_scope_;
    std::unordered_map<std::string, Scope> in_scope_by_block_;
    std::unordered_set<const Instr*> exempt_increment_stores_;

    void error(const std::string& msg) {
        errors_.push_back(RCRError{msg});
    }

    // E0105 (docs/lithon_error_system.md 4c): only int[8|16|32|64] and
    // float[64] exist. A hand-written module can spell any width in its type
    // fields, so this is enforced here and not only in the frontend. Container
    // capacities (list/tuple), dict bucket counts and the future str[N] are
    // capacities, not bit widths, and are deliberately exempt; only scalar
    // int/float -- including a pointer's pointee, which lives in the element
    // slot -- is a width.
    void check_scalar_width(const std::string& kind, int width) {
        const std::string code = "LITHON-E0105: ";
        if (kind == "int") {
            if (width == 8 || width == 16 || width == 32 || width == 64) return;
            if (width < 0)
                error(code + "int requires an explicit width (8, 16, 32 or 64)");
            else
                error(code + "unsupported integer width " + std::to_string(width) +
                      " -- int must be 8, 16, 32 or 64");
        } else if (kind == "float") {
            if (width == 64) return;
            if (width < 0)
                error(code + "float requires an explicit width (64)");
            else
                error(code + "unsupported float width " + std::to_string(width) +
                      " -- float must be 64");
        }
    }

    void add_edge(const std::string& from, const std::string& to) {
        predecessors_[to].push_back(from);
    }

    void build_block_graph() {
        for (size_t i = 0; i < fn_.blocks.size(); ++i) {
            block_index_[fn_.blocks[i].label] = i;
        }
        for (const auto& block : fn_.blocks) {
            for (const auto& instr : block.instrs) {
                if (instr.op == Op::Jump) {
                    add_edge(block.label, instr.name);
                } else if (instr.op == Op::Branch) {
                    size_t comma = instr.name.find(',');
                    add_edge(block.label, instr.name.substr(0, comma));
                    add_edge(block.label, instr.name.substr(comma + 1));
                }
            }
        }
    }

    // Temporary introduced by the frontend when a conditional expression has
    // to be given a home in the IR. Kept in sync with IF_EXPR_TEMP in
    // src/frontend/frontend.py, which mints the names.
    static bool is_lowered_merge_temp(const std::string& name) {
        return name.rfind("__ifexpr", 0) == 0;
    }

    std::unordered_set<const Instr*> find_loop_increment_stores() {
        std::unordered_set<const Instr*> exempt;
        for (const auto& block : fn_.blocks) {
            for (size_t i = 0; i + 3 < block.instrs.size(); ++i) {
                const Instr& load_i  = block.instrs[i];
                const Instr& const_1 = block.instrs[i + 1];
                const Instr& add_i   = block.instrs[i + 2];
                const Instr& store_i = block.instrs[i + 3];

                if (load_i.op != Op::Load) continue;
                if (const_1.op != Op::ConstInt || const_1.int_imm != 1) continue;
                if (add_i.op != Op::Add) continue;
                if (add_i.args.size() != 2) continue;
                if (add_i.args[0] != load_i.result || add_i.args[1] != const_1.result) continue;
                if (store_i.op != Op::Store) continue;
                if (store_i.name != load_i.name) continue;
                if (store_i.args.empty() || store_i.args[0] != add_i.result) continue;

                exempt.insert(&store_i);
            }
        }
        return exempt;
    }

        Scope merge_scopes(const std::vector<const Scope*>& preds, const std::string& block_label) {
        Scope merged;
        if (preds.empty()) return merged;
        if (preds.size() == 1) return *preds[0];

        std::unordered_set<std::string> all_names;
        for (const auto* s : preds)
            for (const auto& [name, t] : *s) all_names.insert(name);

        for (const auto& name : all_names) {
            bool present_everywhere = true;
            const LType* first = nullptr;
            bool disagreement = false;
            for (const auto* s : preds) {
                auto it = s->find(name);
                if (it == s->end()) { present_everywhere = false; continue; }
                if (!first) first = &it->second;
                else if (!(*first == it->second)) disagreement = true;
            }
            if (disagreement) {
                error("type of '" + name + "' disagrees across branches merging into '" +
                      block_label + "' (V1_SPEC 0.6.10)");
                continue;
            }
            if (present_everywhere && first) merged[name] = *first;
        }
        return merged;
    }

    void walk_blocks(const Scope& entry_scope) {
        for (size_t idx = 0; idx < fn_.blocks.size(); ++idx) {
            const BasicBlock& block = fn_.blocks[idx];

            Scope in_scope;
            auto pred_it = predecessors_.find(block.label);
            if (pred_it == predecessors_.end() || pred_it->second.empty()) {
                in_scope = entry_scope;
            } else {
                std::vector<const Scope*> ready_preds;
                for (const auto& pred_label : pred_it->second) {
                    auto bi = block_index_.find(pred_label);
                    if (bi != block_index_.end() && bi->second < idx) {
                        auto os = out_scope_.find(pred_label);
                        if (os != out_scope_.end()) ready_preds.push_back(&os->second);
                    }
                }
                in_scope = ready_preds.empty() ? entry_scope : merge_scopes(ready_preds, block.label);
            }

            in_scope_by_block_[block.label] = in_scope;

            Scope scope = in_scope;
            // 4.2. A tuple is written once, by its own initializer. This tracks
            // whether that has happened yet.
            //
            // Per block and linear on purpose. The initializer is the run of
            // statements right after the declaration, so construction does not
            // usefully cross a block boundary. Resetting per block keeps
            // "still being constructed" a syntactic fact rather than a dataflow
            // property. There is no merge to define, no question of what a join
            // means when one arm built the tuple and the other did not, and no
            // path where a write stays legal because the analysis lost track.
            struct OpenInit {
                int64_t remaining;   // element stores still accepted
                const Instr* decl;   // the declaration that opened it
            };
            std::unordered_map<std::string, OpenInit> open_init;
            // 4.3. A dict literal does not have a fixed entry count, so the count
            // alone cannot tell a finished literal from an unfinished one. The
            // keys seen so far can: construction ends at the first mention, and
            // everything up to that point is the literal. Duplicate keys are
            // rejected here for the same reason, because a table with two values
            // for one key has no single right answer.
            std::unordered_map<std::string, std::vector<int64_t>> open_dict_keys;
            tuple_open_for_init_.clear();
            for (const auto& instr : block.instrs) {
                if (instr.op == Op::Store && instr.type_kind == "tuple" &&
                    instr.args.empty() && instr.type_width > 0) {
                    // A valueless tuple declaration OPENS construction for
                    // exactly `type_width` element stores. Re-declaring the same
                    // name restarts the count rather than extending it.
                    open_init[instr.name] = {instr.type_width, &instr};
                    tuple_open_for_init_.insert(instr.name);
                }
                if (instr.op == Op::Store && instr.type_kind == "dict" &&
                    instr.args.empty() && instr.type_width > 0) {
                    // 4.3. A dict opens construction with no count. Every bucket
                    // starts empty, so there is nothing to count up to; what
                    // ends construction is the first read, exactly as for a tuple.
                    open_init[instr.name] = {0, &instr};
                    tuple_open_for_init_.insert(instr.name);
                    open_dict_keys[instr.name] = {};
                }
                check_instr(instr, scope);

                if (instr.op == Op::IndexStore) {
                    auto it = open_init.find(instr.name);
                    if (it != open_init.end() && --it->second.remaining <= 0) {
                        open_init.erase(it);
                        tuple_open_for_init_.erase(instr.name);
                    }
                    continue;
                }
                if (instr.op == Op::DictStore) {
                    // 4.3. Record the key so a repeat can be rejected, and so the
                    // entries this literal has are known to be exactly the ones
                    // between the declaration and the first read.
                    auto kit = open_dict_keys.find(instr.name);
                    if (kit != open_dict_keys.end() && !instr.args.empty()) {
                        if (const Instr* kc = find_producing_const(instr.args[0])) {
                            std::vector<int64_t>& seen = kit->second;
                            // 4.3. Compared in the same canonical form the two
                            // tiers store and probe with, so "the same key" means
                            // one thing across the whole compiler and a duplicate
                            // cannot slip through here and then collide at run
                            // time. True and 1 are the same key, and a key is
                            // truncated to its declared width.
                            int64_t key = kc->int_imm;
                            if (auto dit = scope.find(instr.name); dit != scope.end())
                                key = dict::canonical_key(key, dit->second.key_kind == "bool",
                                                          dit->second.key_width);
                            const bool dup =
                                std::find(seen.begin(), seen.end(), key) != seen.end();
                            if (dup) {
                                error("duplicate dict key " + std::to_string(kc->int_imm) +
                                      " in the literal for '" + instr.name + "' (4.3)");
                            } else {
                                seen.push_back(kc->int_imm);
                                // 4.3. More entries than buckets cannot be laid
                                // out, because linear probing would have to run
                                // off the end. Rejecting it here means the bound
                                // on the probe can never actually fire on a
                                // program that got this far, which is the point
                                // of keeping it: it is a memory safety net, not a
                                // semantic rule.
                                int64_t buckets = 0;
                                if (auto dit = scope.find(instr.name); dit != scope.end())
                                    buckets = dit->second.width;
                                if (int64_t(seen.size()) > buckets) {
                                    error("'" + instr.name + "' has " +
                                          std::to_string(seen.size()) + " entries but only " +
                                          std::to_string(buckets) +
                                          " buckets (4.3)");
                                }
                            }
                        }
                    }
                    continue;
                }
                // Any other mention of the name ends construction. That covers
                // reading it, loading it, passing it, and assigning to it whole.
                const bool mentions = instr.op == Op::Index ||
                                      instr.op == Op::DictIndex ||
                                      instr.op == Op::DictContains ||
                                      instr.op == Op::Load ||
                                      instr.op == Op::Call ||
                                      (instr.op == Op::Store && !instr.args.empty());
                if (!mentions) continue;
                auto it = open_init.find(instr.name);
                if (it == open_init.end()) continue;
                const OpenInit& st = it->second;
                const int64_t capacity = st.decl->type_width;
                if (st.decl->type_kind == "dict") {
                    // 4.3. A dict ends construction at the first read with no
                    // partial case to diagnose, so there is nothing to say here.
                    open_init.erase(it);
                    open_dict_keys.erase(instr.name);
                    tuple_open_for_init_.erase(instr.name);
                    continue;
                }
                // How construction ended decides everything here.
                //
                // Untouched is a bare declaration and is legal. That is the all
                // zero tuple, and it is why a valueless declaration is not itself
                // a partial initializer.
                //
                // Fully written is a finished literal and is legal.
                //
                // Partially written is neither, and this case is why the rule
                // needs saying out loud. Without it, a declaration of a four slot
                // tuple followed by one store of element zero looks exactly like a
                // one element initializer, so the write would pass as
                // construction. Counting stores does not separate the two. What
                // separates them is that a real literal writes all N slots, and
                // the frontend already rejects a literal of the wrong arity, so
                // a partial one can only be hand written IR.
                if (st.remaining < capacity) {
                    const LType tt{st.decl->type_kind, st.decl->type_width,
                                   st.decl->type_elem_kind, st.decl->type_elem_width};
                    error("'" + instr.name + ": " + type_str(tt) + " initializer wrote " +
                          std::to_string(capacity - st.remaining) + " of " +
                          std::to_string(capacity) +
                          " elements -- a tuple literal has exactly that many, or none "
                          "at all for an all-zero tuple (4.2)");
                }
                open_init.erase(it);
                tuple_open_for_init_.erase(instr.name);
            }
            out_scope_[block.label] = std::move(scope);
        }
    }

    bool reg_type(ValueId id, LType& out) {
        auto it = reg_types_.find(id);
        if (it == reg_types_.end()) return false;
        out = it->second;
        return true;
    }

    const Instr* find_producing_const(ValueId id) {
        for (const auto& block : fn_.blocks)
            for (const auto& instr : block.instrs)
                // 4.3. A bool key is a constant too. It carries its value in the
                // same field, so it belongs here rather than in a second lookup:
                // a dict[bool, ...] whose own literal was rejected as
                // non-constant would be a type no program could ever build.
                if ((instr.op == Op::ConstInt || instr.op == Op::ConstBool) &&
                    instr.result == id)
                    return &instr;
        return nullptr;
    }

    const Instr* find_producing_instr(ValueId id) {
        for (const auto& block : fn_.blocks)
            for (const auto& instr : block.instrs)
                if (instr.result == id) return &instr;
        return nullptr;
    }

    void check_assignment_compatible(const LType& source, const LType& target,
                                      const std::string& context) {
        // 4.4. A pointer is compatible only with a pointer over the SAME
        // pointee. The general same-kind branch below would accept any two
        // pointers -- both have width -1, so `source.width < 0 && target.width
        // < 0` returns true -- and `ptr[int[64]]` from `ptr[float[64]]` would
        // pass, which is exactly the class of mistake a typed pointer exists
        // to make impossible.
        if (target.is_ptr() || source.is_ptr()) {
            if (source.kind == "ptr" && target.kind == "ptr") {
                if (source.element() == target.element()) return;
                error(context + ": cannot convert " + type_str(source) + " to " +
                      type_str(target) +
                      " -- pointers must point at the same pointee (4.4)");
                return;
            }
            error(context + ": cannot convert " + type_str(source) + " to " +
                  type_str(target) +
                  " -- a pointer can only be built by addressof and only flows into "
                  "another pointer of the same pointee (4.4)");
            return;
        }
        if (source.kind == target.kind) {
            if (source.width < 0 && target.width < 0) return;
            if (source.width >= 0 && target.width >= 0) {
                if (target.width >= source.width) return;
                error(context + ": cannot narrow " + type_str(source) + " into " +
                      type_str(target) + " -- narrowing is never allowed (V1_SPEC 0.6.11)");
                return;
            }
            error(context + ": incompatible " + type_str(source) + " and " + type_str(target));
            return;
        }
        if (source.kind == "int" && target.kind == "float") return;
        if (source.kind == "float" && target.kind == "int") {
            error(context + ": float -> int conversion does not exist in Lithon "
                  "(V1_SPEC 0.6.11) -- no cast can perform this");
            return;
        }
        error(context + ": cannot convert " + type_str(source) + " to " + type_str(target) +
              " -- no such conversion exists");
    }

    bool operand_range(ValueId id, int64_t& lo, int64_t& hi) {
        if (const Instr* c = find_producing_const(id)) { lo = hi = c->int_imm; return true; }
        LType t;
        if (!reg_type(id, t) || t.kind != "int") return false;
        auto [l, h] = int_range(t.width);
        lo = l; hi = h;
        return true;
    }

    void check_binop_fits_target(const Instr& binop_instr, const LType& target,
                                  const std::string& context) {
        if (target.kind != "int") return;
        if (target.width >= 64) return;

        // A left shift multiplies, so `x:int[8] = 100; x = x << 3` would put
        // 800 in a variable whose declared range is -128..127. Since the
        // shift count is a constant here, that is decidable now. __int128 is
        // used for the intermediate because the widened result can leave
        // int64 entirely (int[8] << 63), and clamping before the comparison
        // would be exactly the kind of quiet wrong answer this pass exists
        // to prevent.
        if (binop_instr.op == Op::Shl) {
            int64_t k_lo, k_hi;
            if (!operand_range(binop_instr.args.at(1), k_lo, k_hi)) return;
            if (k_lo < 0 || k_hi > 63) return;   // runtime traps; not ours to range-check
            int64_t lo, hi;
            if (!operand_range(binop_instr.args.at(0), lo, hi)) return;
            // Shifting left magnifies, so the most negative corner is
            // lo << k_hi and the most positive is hi << k_lo.
            __int128 r_lo = static_cast<__int128>(lo) << k_hi;
            __int128 r_hi = static_cast<__int128>(hi) << k_lo;
            auto [target_lo, target_hi] = int_range(target.width);
            if (r_lo < target_lo || r_hi > target_hi) {
                error(context + ": type " + type_str(target) + " is not wide enough -- `x << " +
                      std::to_string(k_lo) + "` can produce up to " +
                      wide_str(r_hi) + ", which exceeds " + type_str(target) +
                      "'s range " + std::to_string(target_lo) + ".." + std::to_string(target_hi) +
                      ". Declare a wider type explicitly (V1_SPEC 0.5, 0.6.5) -- the compiler "
                      "will not auto-widen it for you.");
            }
            return;
        }

        if (binop_instr.op != Op::Add && binop_instr.op != Op::Sub && binop_instr.op != Op::Mul) return;

        int64_t lo1, hi1, lo2, hi2;
        if (!operand_range(binop_instr.args.at(0), lo1, hi1)) return;
        if (!operand_range(binop_instr.args.at(1), lo2, hi2)) return;

        int64_t possible_lo, possible_hi;
        if (binop_instr.op == Op::Add) { possible_lo = lo1 + lo2; possible_hi = hi1 + hi2; }
        else if (binop_instr.op == Op::Sub) { possible_lo = lo1 - hi2; possible_hi = hi1 - lo2; }
        else {
            int64_t corners[4] = {lo1*lo2, lo1*hi2, hi1*lo2, hi1*hi2};
            possible_lo = *std::min_element(corners, corners + 4);
            possible_hi = *std::max_element(corners, corners + 4);
        }

        auto [target_lo, target_hi] = int_range(target.width);
        if (possible_lo < target_lo || possible_hi > target_hi) {
            error(context + ": type " + type_str(target) + " is not wide enough -- this "
                  "operation can produce " + std::to_string(possible_lo) + ".." +
                  std::to_string(possible_hi) + ", which exceeds " + type_str(target) +
                  "'s range " + std::to_string(target_lo) + ".." + std::to_string(target_hi) +
                  ". Declare a wider type explicitly (V1_SPEC 0.5, 0.6.5) -- the compiler "
                  "will not auto-widen it for you.");
        }
    }

    // 4.4. The pointer half of Add/Sub/Mul typing. Add/Sub of a pointer and a
    // literal, element-scaled byte offset keeps the pointer; everything else is
    // refused. The frontend lowers `_p + 1` to `add _p, sizeof(T)` during
    // lowering, so at this level the offset is already a byte count, and the
    // multiple-of-stride check below is the backstop that names the error for
    // hand-written IR instead of quietly producing a mid-element pointer.
    void check_ptr_arith(const Instr& instr, const LType& lhs, const LType& rhs) {
        if (instr.op == Op::Mul) {
            error("cannot multiply a pointer -- pointer arithmetic is add/sub of "
                  "a literal offset only (4.4)");
            return;
        }
        const bool ptr_lhs = lhs.kind == "ptr";
        const LType& off = ptr_lhs ? rhs : lhs;
        if (off.kind != "int") {
            error("a pointer offset must be an int, got " + type_str(off) + " (4.4)");
            return;
        }
        const LType& p = ptr_lhs ? lhs : rhs;
        const Instr* c = find_producing_const(instr.args.at(ptr_lhs ? 1 : 0));
        if (!c) {
            error("a pointer offset must be a literal -- `_p + expr` has no defined "
                  "meaning at runtime (4.4)");
            return;
        }
        const int64_t bytes = c->int_imm;
        if ((bytes % p.byte_stride()) != 0) {
            error("pointer offset " + std::to_string(bytes) +
                  " is not a multiple of the pointee stride " +
                  std::to_string(p.byte_stride()) + " (4.4)");
            return;
        }
        reg_types_[instr.result] = p;
    }

    void check_value_into_target(ValueId id, const LType& target, const std::string& context) {
        if (const Instr* c = find_producing_const(id)) {
            if (target.kind == "int") {
                auto [lo, hi] = int_range(target.width);
                if (c->int_imm < lo || c->int_imm > hi) {
                    error(context + ": literal " + std::to_string(c->int_imm) + " does not fit " +
                          type_str(target) + " (valid range " + std::to_string(lo) + ".." +
                          std::to_string(hi) + ") -- V1_SPEC 0.6.5");
                }
                return;
            }
            // An int literal is not a float, and a float-typed location has to
            // hold a float. This used to fall out of the int branch above and
            // return, so `j: float[64] = 0` was accepted here and only blew up
            // much later in codegen, where the variable had been written once
            // as an int (here) and once as a float (the loop), joined to
            // Unknown, and the print guard refused with a message about a
            // "value stored both an int and a float" that never mentioned the
            // literal the user actually wrote.
            //
            // Expression-level promotion is a different rule and is untouched:
            // Op::Add/Sub/Mul/Mod infer their result type directly (see
            // check_instr) and never come through here, so `7 + 0.5` is
            // still 7.5. What this forbids is storing an int into a
            // float-typed variable, which is the declaration being a
            // contract about what the variable holds.
            if (target.kind == "float" && c->op == Op::ConstInt) {
                error(context + ": literal " + std::to_string(c->int_imm) + " is an int, but " +
                      type_str(target) + " must hold a float -- write " +
                      lithon::jit::host_format_double(static_cast<double>(c->int_imm)) +
                      " (V1_SPEC 0.6.11)");
                return;
            }
            // 4.1. A scalar literal cannot build a container. This branch used
            // to fall through and accept, so `store ys, %int_literal :
            // list[float[64],4]` type-checked clean and the declaration became
            // a contract about nothing. The old tests could not catch it: every
            // container case fed it an int and expected rejection, which was
            // then being granted for the wrong reason elsewhere.
            if (!target.is_scalar() &&
                (c->op == Op::ConstInt || c->op == Op::ConstFloat || c->op == Op::ConstBool)) {
                // 4.4. A pointer gets its own diagnosis: there is no null
                // literal, so a constant into a ptr has exactly one message.
                if (target.is_ptr()) {
                    error(context + ": a literal cannot build " + type_str(target) +
                          " -- a pointer needs an addressof, and there is no null "
                          "pointer in Lithon (4.4)");
                    return;
                }
                error(context + ": a literal cannot build " + type_str(target) +
                      " -- containers need an IndexStore (4.1)");
                return;
            }
            return;
        }
        if (const Instr* producer = find_producing_instr(id)) {
            if (producer->op == Op::Add || producer->op == Op::Sub || producer->op == Op::Mul
                || producer->op == Op::Shl) {
                check_binop_fits_target(*producer, target, context);
                // No return here. check_binop_fits_target answers "can the
                // values this produces FIT the target", which is not the same
                // question as "does the result's declared width fit". An int[64]
                // result can be numerically small enough for int[16] and still
                // be an illegal narrowing, and returning early skipped that
                // check entirely, so `def f() -> int[16]: return 100 + 100`
                // was accepted here while the Python checker rejected it.
            }
        }
        LType source;
        if (reg_type(id, source)) {
            // The literal rule above, extended to every other int value: a
            // float-typed location must hold a float. Codegen never converts on
            // a store, so an int register written into a float variable leaves
            // that variable stored as both kinds -- joined to Unknown by the
            // print guard, and lowered as an integer by lithon_jit, which has
            // no guard. Expression-level promotion (`n + 0.5`) is unaffected:
            // Add/Sub/Mul/Mod infer a float result directly, so `x = n + 0.0`
            // is the way to write the conversion.
            if (target.kind == "float" && source.kind == "int") {
                error(context + ": " + type_str(source) + " value stored into " + type_str(target) +
                      " -- a float-typed location must hold a float; Lithon does not convert "
                      "on assignment (V1_SPEC 0.6.11)");
                return;
            }
            check_assignment_compatible(source, target, context);
        }
    }

    void check_print_call(const Instr& instr) {
        if (instr.args.size() != 1) {
            error("print() with exactly one argument is supported");
            return;
        }
        LType t;
        if (!reg_type(instr.args.at(0), t)) return;
        // 4.4. ptr[T] prints as a hexadecimal address (0x7ffd...), the way C's
        // %p and Rust's {:p} do. It is an address, not the pointee: print(p)
        // shows where, print(valueof(p)) shows what.
        static const std::unordered_set<std::string> allowed = {"int", "float", "str", "bool", "ptr"};
        if (allowed.find(t.kind) == allowed.end()) {
            error("print() does not accept " + type_str(t) + " -- V1_SPEC 0.6.9's closed "
                  "overload set is int[N], float[N], str[N], bool and ptr[T] only");
        }
    }

    void check_user_call(const Instr& instr) {
        auto it = all_fns_.find(instr.name);
        if (it == all_fns_.end()) {
            error("call to unknown function '" + instr.name + "'");
            return;
        }
        const FnMeta& callee = it->second;
        if (instr.args.size() != callee.param_types.size()) {
            error("'" + instr.name + "' expects " + std::to_string(callee.param_types.size()) +
                  " argument(s), got " + std::to_string(instr.args.size()) + " (V1_SPEC 0.6.8)");
            return;
        }
        for (size_t i = 0; i < instr.args.size(); ++i) {
            check_value_into_target(instr.args[i], callee.param_types[i],
                "argument " + std::to_string(i + 1) + " to '" + instr.name + "'");
        }
        if (instr.result != kInvalidValue && callee.has_return_type) {
            reg_types_[instr.result] = callee.return_type;
        }
    }

    void check_instr(const Instr& instr, Scope& scope) {
        // E0105: refuse unsupported scalar widths wherever they appear on the
        // instruction (a storage target, a value type such as valueof's pointee,
        // a container element or a dict key/value). Runs before the op logic so
        // the refusal can never be masked by a later, more specific check.
        check_scalar_width(instr.type_kind, instr.type_width);
        check_scalar_width(instr.type_elem_kind, instr.type_elem_width);
        check_scalar_width(instr.type_key_kind, instr.type_key_width);
        switch (instr.op) {
            // 4.1. A const takes its width from the trailing " : T[N]" suffix
            // when one is present, and defaults to int[64] otherwise. Hardcoding
            // 64 unconditionally made every narrower element type unreachable
            // from IR -- there was no way to write a single int[32] value, so
            // list[int[32],N] could be type-declared but never exercised, and
            // the packed-stride frame layout it depends on went untested.
            case Op::ConstInt:
                reg_types_[instr.result] =
                    instr.type_kind == "int" && instr.type_width > 0
                        ? LType{"int", instr.type_width}
                        : LType{"int", 64};
                return;
            case Op::ConstFloat: reg_types_[instr.result] = LType{"float", 64}; return;
            case Op::ConstBool:  reg_types_[instr.result] = LType{"bool", -1}; return;
            case Op::Load: {
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                reg_types_[instr.result] = it->second;
                return;
            }
            // 4.4. addressof names a plain scalar variable and yields the ptr[T]
            // that round-trips through valueof. Containers and dicts have no
            // single storage cell to point at; a pointer has no storage of its
            // own at all.
            case Op::AddressOf: {
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                const LType& t = it->second;
                if (t.is_ptr()) {
                    error("cannot take the address of " + type_str(t) +
                          " -- a pointer is an address with no storage of its own (4.4)");
                    return;
                }
                if (!t.is_scalar()) {
                    error("cannot take the address of " + type_str(t) +
                          " -- addressof takes a plain scalar variable (4.4)");
                    return;
                }
                reg_types_[instr.result] = LType{"ptr", -1, t.kind, t.width, "", -1};
                return;
            }
            // 4.4. valueof loads the pointee through a pointer. The instruction's
            // trailing " : T" suffix is the pointee, and it must match what the
            // pointer actually points at -- that suffix is what names the width
            // of the native load, so a mismatch would read the wrong number of
            // bytes and the checker must not bless it.
            case Op::ValueOf: {
                LType p;
                if (!reg_type(instr.args.at(0), p) || p.kind != "ptr") {
                    error("valueof needs a pointer, got " +
                          (p.kind.empty() ? std::string("an unresolved value") : type_str(p)) +
                          " (4.4)");
                    return;
                }
                const LType pointee{instr.type_kind, instr.type_width, "", -1, "", -1};
                if (pointee != p.element()) {
                    error("valueof of " + type_str(p) + " yields " + type_str(p.element()) +
                          ", but this instruction is annotated " + type_str(pointee) + " (4.4)");
                    return;
                }
                reg_types_[instr.result] = p.element();
                return;
            }
            // 4.1. Container access. The shared preconditions are: the name must
            // be a live binding, it must actually BE a container (indexing a
            // scalar is a type error, not an address-of-value accident), and a
            // LITERAL index is range-checked now because N is static -- this is
            // the "static-only capacity overflow" diagnostic.
            case Op::Index:
            case Op::IndexStore:
            case Op::Len: {
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                const LType& ct = it->second;
                if (ct.kind != "list" && ct.kind != "tuple") {
                    // 4.3. A dict is a container but not an array. It has no
                    // index and no length, so reaching here means the frontend
                    // picked the wrong opcode for it, and naming the dict
                    // operations is the useful thing to say.
                    if (ct.is_dict()) {
                        if (instr.op == Op::IndexStore) {
                            error("cannot store into " + type_str(ct) +
                                  " -- a dict is immutable (4.3); it is filled "
                                  "by its own literal, and after that the only "
                                  "operations are a read and contains");
                            return;
                        }
                        error(instr.name + " is a dict, which has no index and no "
                              "length (4.3) -- read it as " + instr.name +
                              "[key] or ask contains(" + instr.name + ", key)");
                        return;
                    }
                    error(instr.name + " is " + type_str(ct) + ", not a container -- " +
                          (instr.op == Op::Len ? "len()" : "indexing") +
                          " needs a list or tuple");
                    return;
                }
                // 4.2. Immutability, enforced here and nowhere else.
                //
                // A tuple is laid out exactly like a list. Same packed stride,
                // same Index read, one representation in the IR. The only thing
                // that makes it a tuple is that this store is refused.
                //
                // Keeping the rule in the checker rather than in codegen means
                // the illegal program is rejected before it becomes machine
                // code. There is no runtime immutability check to pay for. A
                // tuple is a list that the source language cannot write
                // through.
                //
                // A literal index is refused for the same reason a runtime one
                // is. There is no "it is constant so allow it" case, because a
                // program that can write t[0] at compile time can write it at
                // run time too.
                if (instr.op == Op::IndexStore && ct.kind == "tuple" &&
                    !tuple_open_for_init_.count(instr.name)) {
                    error("cannot store into " + type_str(ct) + " -- a tuple is immutable "
                          "(4.2); a tuple is filled by its own literal, and after that "
                          "the only operation on one is an indexed read");
                    return;
                }
                if (instr.op == Op::Len) {
                    // 4.2 is indexed reads only, so len() on a tuple is
                    // rejected rather than quietly accepted.
                    //
                    // Implementing it would be free. N is static, so it would
                    // fold to a literal exactly as len() on a list does. It is
                    // still a separate decision, and adding an operation just
                    // because it happens to be easy is how a type ends up
                    // promising something the language never agreed to.
                    if (ct.kind == "tuple") {
                        error("len() on " + type_str(ct) + " is not part of 4.2 -- "
                              "a tuple's length is its capacity, and the only "
                              "operation on one is an indexed read");
                        return;
                    }
                    // Folded to a constant by codegen, but it is still typed as a
                    // full-width int so arithmetic on it behaves like any other.
                    reg_types_[instr.result] = LType{"int", 64};
                    return;
                }

                const ValueId idx = instr.args.at(0);
                if (instr.op == Op::IndexStore && instr.args.size() < 2) {
                    error("IndexStore needs an index and a value");
                    return;
                }
                LType idx_t;
                if (reg_type(idx, idx_t) && idx_t.kind != "int") {
                    error("index into " + type_str(ct) + " must be an int, got " +
                          type_str(idx_t));
                    return;
                }
                // Static overflow. Only a LITERAL index is decidable here; a
                // runtime index is checked in codegen, not guessed at here.
                if (const Instr* c = find_producing_const(idx)) {
                    const int64_t n = c->int_imm;
                    if (n < 0 || n >= ct.width) {
                        error("index " + std::to_string(n) + " is out of range for " +
                              type_str(ct) + " -- valid indices are 0.." +
                              std::to_string(ct.width - 1));
                        return;
                    }
                }
                if (instr.op == Op::Index) {
                    reg_types_[instr.result] = ct.element();
                } else {
                    // 4.1. Element stores go through check_value_into_target, the
                    // same path a scalar `x: int[32] = 11` takes, so a literal
                    // narrows into a narrower element when it fits and a runtime
                    // value still may not. Checking `val_t != ct.element()`
                    // instead made the two disagree in a way nothing explained:
                    // `x: int[32] = 11` compiled and `xs[0] = 11` into a
                    // list[int[32],4] did not, which left every packed-stride
                    // element width unreachable from the frontend, because the
                    // frontend cannot attach a width to an int literal.
                    check_value_into_target(instr.args.at(1), ct.element(),
                                            "store into " + type_str(ct));
                }
                return;
            }
            // 4.3. Dict access. The name must be a dict, and the key must be the
            // dict's key type. Everything else about the table is fixed by the
            // type, so there is nothing else to decide here: N buckets, a
            // Fibonacci hash and a linear probe are all implied by dict[K,V,N].
            case Op::DictStore:
            case Op::DictIndex:
            case Op::DictContains: {
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                const LType& dt = it->second;
                if (!dt.is_dict()) {
                    error(instr.name + " is " + type_str(dt) + ", not a dict (4.3)");
                    return;
                }
                // 4.3. Immutability, the same rule a tuple has. A dict is filled
                // by its own literal and never written again, so a DictStore
                // outside construction is refused here rather than in codegen.
                if (instr.op == Op::DictStore &&
                    !tuple_open_for_init_.count(instr.name)) {
                    error("cannot store into " + type_str(dt) + " -- a dict is "
                          "immutable (4.3); it is filled by its own literal, and "
                          "after that the only operations are a read and contains");
                    return;
                }
                const std::string what =
                    instr.op == Op::DictContains ? "contains" : "key";
                const ValueId key_id = instr.args.at(0);
                LType kt;
                if (!reg_type(key_id, kt)) return;
                // 4.3. A key is checked the way a list index is, not by exact
                // type equality. The frontend cannot attach a width to an int
                // literal, so `d[1]` into a dict[int[8], ...] hands over an
                // int[64] and demanding int[8] exactly would make every narrow
                // key type unreachable: the key literal would be rejected by its
                // own dict.
                //
                // What is required is that the key CAN BE the declared one, so a
                // literal is range checked and a wider int is refused unless it is
                // exactly the width the table stores.
                if (dt.key_kind == "bool") {
                    if (kt.kind != "bool") {
                        error(what + " into " + type_str(dt) + " must be " +
                              type_str(dt.key()) + ", got " + type_str(kt));
                        return;
                    }
                } else {
                    if (kt.kind != "int") {
                        error(what + " into " + type_str(dt) + " must be " +
                              type_str(dt.key()) + ", got " + type_str(kt));
                        return;
                    }
                    if (const Instr* c = find_producing_const(key_id)) {
                        auto [lo, hi] = int_range(dt.key_width);
                        if (c->int_imm < lo || c->int_imm > hi) {
                            error(what + " " + std::to_string(c->int_imm) +
                                  " into " + type_str(dt) + " does not fit " +
                                  type_str(dt.key()) + " (valid range " +
                                  std::to_string(lo) + ".." + std::to_string(hi) + ")");
                            return;
                        }
                    } else if (kt.width > dt.key_width) {
                        error(what + " into " + type_str(dt) + " must be " +
                              type_str(dt.key()) + ", got " + type_str(kt));
                        return;
                    }
                }
                if (instr.op == Op::DictStore) {
                    // The key has to be a constant, because the bucket is its
                    // hash and the whole design is that construction resolves
                    // buckets while compiling rather than probing at run time.
                    if (!find_producing_const(key_id)) {
                        error("a dict key must be a constant, so its bucket can be "
                              "decided at compile time (4.3)");
                        return;
                    }
                    if (instr.args.size() < 2) {
                        error("DictStore needs a key and a value");
                        return;
                    }
                    check_value_into_target(instr.args.at(1), dt.element(),
                                            "store into " + type_str(dt));
                    return;
                }
                if (instr.op == Op::DictContains) {
                    reg_types_[instr.result] = LType{"bool", -1};
                } else {
                    reg_types_[instr.result] = dt.element();
                }
                return;
            }
            case Op::Add:
            case Op::Sub:
            case Op::Mul: {
                LType lhs, rhs;
                if (!reg_type(instr.args.at(0), lhs) || !reg_type(instr.args.at(1), rhs)) return;
                // 4.4. Pointer arithmetic is add/sub of a literal element-scaled
                // offset. Only Add/Sub, only one pointer operand, only an int
                // offset, and only a CONSTANT offset -- the frontend scales the
                // element count to bytes during lowering, so a runtime offset has
                // no defined meaning here and is rejected loudly.
                if (lhs.kind == "ptr" || rhs.kind == "ptr") {
                    check_ptr_arith(instr, lhs, rhs);
                    return;
                }
                if (lhs.kind == "float" || rhs.kind == "float") reg_types_[instr.result] = LType{"float", 64};
                else if (lhs.kind == "int" && rhs.kind == "int") {
                    reg_types_[instr.result] = LType{"int", std::max(lhs.width, rhs.width)};
                    // E0303 Tier 1: a constant add/sub/mul whose exact result
                    // leaves int64 is refused HERE rather than ever reaching a
                    // runtime trap or silently wrapping. This is also what keeps
                    // the optimizer's fold_constants safe: it folds int adds
                    // with wrap semantics (that is what the machine code did),
                    // and can only ever see a non-overflowing pair after this
                    // gate. Narrow ints are exempt by construction: their
                    // declared range cannot fit an int64 overflow, and the
                    // store-narrowing pass already refuses such results.
                    if (lhs.width == 64 || rhs.width == 64) {
                        const Instr* ca = find_producing_const(instr.args.at(0));
                        const Instr* cb = find_producing_const(instr.args.at(1));
                        if (ca && cb) {
                            int64_t r = 0;
                            const bool ovf =
                                instr.op == Op::Add ? __builtin_add_overflow(ca->int_imm, cb->int_imm, &r)
                                : instr.op == Op::Sub ? __builtin_sub_overflow(ca->int_imm, cb->int_imm, &r)
                                                      : __builtin_mul_overflow(ca->int_imm, cb->int_imm, &r);
                            if (ovf) {
                                const char* opn = instr.op == Op::Add
                                    ? "add" : instr.op == Op::Sub ? "sub" : "mul";
                                const char* wrapn = instr.op == Op::Add
                                    ? "wrap_add" : instr.op == Op::Sub ? "wrap_sub" : "wrap_mul";
                                error("LITHON-E0303: constant " + std::string(opn) +
                                      " overflows int[64] -- " + std::to_string(ca->int_imm) +
                                      " and " + std::to_string(cb->int_imm) +
                                      " combine outside the int64 range; use " + wrapn +
                                      "() to wrap instead (E0303)");
                            }
                        }
                    }
                }
                return;
            }
            case Op::WrapAdd:
            case Op::WrapSub:
            case Op::WrapMul: {
                // E0303 opt-out. Wrapping arithmetic, int[64] operands only in
                // v1: narrow ints can never overflow int64 so wrapping them is
                // meaningless, and float wrap does not exist.
                LType lhs, rhs;
                if (!reg_type(instr.args.at(0), lhs) || !reg_type(instr.args.at(1), rhs)) return;
                const char* name = instr.op == Op::WrapAdd ? "wrap_add"
                                 : instr.op == Op::WrapSub ? "wrap_sub" : "wrap_mul";
                if (lhs.kind == "ptr" || rhs.kind == "ptr" ||
                    lhs.kind != "int" || rhs.kind != "int" ||
                    lhs.width != 64 || rhs.width != 64) {
                    error("LITHON-E0303: " + std::string(name) +
                          "() needs two int[64] operands -- narrow ints never overflow "
                          "and a float cannot wrap (E0303, v1 int[64] only)");
                    return;
                }
                reg_types_[instr.result] = LType{"int", 64};
                return;
            }
            case Op::Mod:
                // Shaped like Mul, NOT like Div. Div unconditionally widens
                // because a quotient generally is not an integer; a remainder
                // never leaves the domain, so int % int stays int. Typing it
                // as Div would mean `7 % 2` is 1.0, and typing it as anything
                // that narrows would contradict the "narrowing is never
                // allowed" invariant enforced above.
                {
                    LType lhs, rhs;
                    if (!reg_type(instr.args.at(0), lhs) || !reg_type(instr.args.at(1), rhs)) return;
                    if (lhs.kind == "float" || rhs.kind == "float") reg_types_[instr.result] = LType{"float", 64};
                    else if (lhs.kind == "int" && rhs.kind == "int")
                        reg_types_[instr.result] = LType{"int", std::max(lhs.width, rhs.width)};
                    return;
                }
            case Op::Div:
                // Per V1_SPEC 0.2: any division is true division and
                // always produces float.
                {
                    LType lhs, rhs;
                    if (reg_type(instr.args.at(0), lhs) && (lhs.kind == "ptr" ||
                        (reg_type(instr.args.at(1), rhs) && rhs.kind == "ptr"))) {
                        error("pointer division does not exist -- pointer arithmetic "
                              "is add/sub of a literal offset only (4.4)");
                        return;
                    }
                    reg_types_[instr.result] = LType{"float", 64};
                }
                return;
            case Op::Lt: case Op::Gt: {
                LType lhs, rhs;
                if ((reg_type(instr.args.at(0), lhs) && lhs.kind == "ptr") ||
                    (reg_type(instr.args.at(1), rhs) && rhs.kind == "ptr")) {
                    error("a pointer is an address, not an int -- comparing one with "
                          "< or > has no meaning; only == against a literal or another "
                          "pointer is defined (4.4)");
                    return;
                }
                reg_types_[instr.result] = LType{"bool", -1};
                return;
            }
            case Op::Eq: {
                LType lhs, rhs;
                const bool lhs_ptr = reg_type(instr.args.at(0), lhs) && lhs.kind == "ptr";
                const bool rhs_ptr = reg_type(instr.args.at(1), rhs) && rhs.kind == "ptr";
                if (lhs_ptr || rhs_ptr) {
                    // 4.4. `_p == 0` is the README's ordinary integer compare, and
                    // comparing two addresses is the same integer compare. A
                    // pointer against a runtime int is refused, because the value
                    // an address would be compared with is not an address anybody
                    // can name.
                    if (lhs_ptr && rhs_ptr) { reg_types_[instr.result] = LType{"bool", -1}; return; }
                    const ValueId other_id = lhs_ptr ? instr.args.at(1) : instr.args.at(0);
                    if (find_producing_const(other_id)) {
                        reg_types_[instr.result] = LType{"bool", -1};
                        return;
                    }
                    error("a pointer can only be compared with a literal integer "
                          "(e.g. `_p == 0`) or with another pointer (4.4)");
                    return;
                }
                reg_types_[instr.result] = LType{"bool", -1};
                return;
            }
            case Op::And: case Op::Or: case Op::Not:
                reg_types_[instr.result] = LType{"bool", -1};
                return;
            case Op::Shl: case Op::Shr:
            case Op::BitAnd: case Op::BitOr: case Op::BitXor: {
                // Integer-only, and the result mirrors the LEFT operand's
                // width rather than the max of both like Add/Sub/Mul do.
                //   band/bor/bxor: AND/OR/XOR can only CLEAR bits, so the
                //     result is representable in either operand, and taking
                //     the left one never narrows below a value that fits.
                //   shl/shr:       the left operand is the value being scaled,
                //     so its width is the floor. `x << k` can exceed it, and
                //     that is caught separately -- by the literal-count range
                //     check in check_binop_fits_target, or at runtime for a
                //     dynamic count. Reporting the result as the left width
                //     here is what makes that check reachable.
                LType lhs, rhs;
                if (!reg_type(instr.args.at(0), lhs) || !reg_type(instr.args.at(1), rhs)) return;
                if (lhs.kind != "int" || rhs.kind != "int") {
                    const LType& bad = lhs.kind != "int" ? lhs : rhs;
                    error("bitwise operand has type " + type_str(bad) + " -- shl/shr/and/or/xor "
                          "are integer-only; Lithon has no float bit pattern to reinterpret "
                          "(V1_SPEC 0.6.11)");
                    return;
                }
                if (instr.op == Op::Shl || instr.op == Op::Shr) {
                    // The static half of the two-layer shift-count backstop.
                    // x86 masks the count to 6 bits, so 64 would execute as 0
                    // and return the unshifted value; and a negative count
                    // would use the masked low bits. Neither is worth
                    // executing when the answer is already known here. The
                    // interpreter's apply_bitop re-checks this at runtime,
                    // which is what covers a non-constant count.
                    if (const Instr* c = find_producing_const(instr.args.at(1))) {
                        if (c->int_imm < 0 || c->int_imm > 63) {
                            error(std::string("shift count ") + std::to_string(c->int_imm) +
                                  " is out of range 0..63 for `" +
                                  (instr.op == Op::Shl ? "<<`" : ">>`") +
                                  " -- the machine word is 64 bits and a count outside 0..63 "
                                  "has no defined meaning (V1_SPEC 0.6.11)");
                            return;
                        }
                    }
                }
                reg_types_[instr.result] = LType{"int", lhs.width};
                return;
            }
            case Op::Store: {
                // 0.6.10. `i = i + 1` (and its augmented spelling) lowers to
                // load / const 1 / add / store, and that canonical loop
                // increment is exempt from the value conversion check. The
                // exemption has to sit before the annotation branch too: a
                // declared counter now reaches here as an ANNOTATED store
                // (`i: int[8]` ... `i = i + 1`), and re-checking the widened
                // iadd result against the declared width would reject the
                // counter. The load in the pattern already proved the name
                // assigned, so skipping binds nothing new.
                if (exempt_increment_stores_.count(&instr)) return;
                if (instr.type_kind.empty()) {
                    auto it = scope.find(instr.name);
                    if (it == scope.end()) {
                        // A conditional expression lowers to a temporary that
                        // both arms store and the join loads. The name is
                        // generated by the frontend (see IRBuilder.build_expr
                        // in src/frontend/frontend.py), carries no source
                        // annotation because there is no source variable to
                        // annotate, and its type is whatever the arms agree on
                        // -- which merge_scopes below then checks, so an int
                        // arm meeting a float arm is still an error.
                        if (is_lowered_merge_temp(instr.name)) {
                            LType v;
                            if (reg_type(instr.args.at(0), v)) scope[instr.name] = v;
                            return;
                        }
                        error("'" + instr.name + "' is assigned without a type annotation "
                              "(V1_SPEC 0.6.1) -- write '" + instr.name + ": <type> = ...' first");
                        return;
                    }
                    check_value_into_target(instr.args.at(0), it->second,
                                             "re-assignment of '" + instr.name + "'");
                    return;
                }
                const LType declared = declared_type_of(instr);
                if (declared.kind.empty()) return;   // already reported
                // 4.4. `_`-prefix enforcement, the name-and-type side of 4.4's
                // naming rule: source names starting with `_` must be pointers,
                // and pointer-type declarations must carry an `_`-prefixed name.
                // The frontend enforces this on source too; here it is a
                // backstop for hand-written IR. Lowered merge temps are exempt:
                // the frontend generates `__ifexpr...` names that are not
                // pointers and cannot help the spellings they start with.
                if (!is_lowered_merge_temp(instr.name)) {
                    if (declared.is_ptr() && instr.name[0] != '_') {
                        error("'" + instr.name + "' is a pointer but does not start with "
                              "'_' -- 4.4's naming rule: a pointer is named `_p`, its "
                              "first character is the underscore (4.4)");
                        return;
                    }
                    if (instr.name[0] == '_' && !declared.is_ptr()) {
                        error("'" + instr.name + "': a name that starts with '_' must be "
                              "a pointer, but it is declared " + type_str(declared) + " (4.4)");
                        return;
                    }
                }
                // 0.6.10. A valueless store whose type is a SCALAR or a POINTER
                // is a type-only declaration -- `i: int[8]` or `_p: ptr[T]`
                // written with no value. The frontend consumes the declaration
                // (it records the type and carries it into the first
                // assignment's own annotation), so this instruction has no
                // value to write and binds nothing: the name stays UNASSIGNED
                // until a real store, and a read before that is the ordinary
                // "not definitely assigned" (0.6.10). That is also what keeps
                // a bare pointer from ever reading as null -- there is no null
                // pointer in Lithon (4.4), only an unassigned one.
                if (instr.args.empty() && (declared.is_ptr() || declared.is_scalar()))
                    return;
                // 4.1. A valueless container store is a DECLARATION: it reserves
                // storage and binds the name. Its shape was already validated
                // above; there is no value to check into it, and running the
                // conversion check here would demand a list out of thin air.
                if (instr.args.empty() && !declared.is_scalar()) {
                    scope[instr.name] = declared;
                    return;
                }
                check_value_into_target(instr.args.at(0), declared,
                                         "declaration of '" + instr.name + "'");
                scope[instr.name] = declared;
                return;
            }
            case Op::Call: {
                if (instr.name == "print") {
                    check_print_call(instr);
                } else {
                    check_user_call(instr);
                }
                return;
            }
            case Op::Return: {
                if (instr.args.empty()) return;
                if (!has_return_type_) return;
                check_value_into_target(instr.args.at(0), return_type_,
                                         "return in '" + fn_.name + "'");
                return;
            }
            default:
                return;
        }
    }

    // 4.1. Builds the recursive type for a declaration, rejecting a container
    // whose shape is missing rather than letting it degrade into a bare
    // kind+capacity. `list` with no element would otherwise become
    // LType{"list", 10, "", -1} -- indistinguishable from a list whose element
    // failed to parse, and every later equality check would pass it.
    LType declared_type_of(const Instr& instr) {
        LType t{instr.type_kind, instr.type_width, instr.type_elem_kind,
                 instr.type_elem_width, instr.type_key_kind, instr.type_key_width};
        const bool container = t.kind == "list" || t.kind == "tuple" ||
                               t.kind == "ptr" || t.kind == "dict";

        if (t.kind == "list" || t.kind == "tuple") {
            if (t.elem_kind.empty()) {
                error("'" + instr.name + ": " + t.kind +
                      " needs an element type, e.g. " + t.kind + "[int[64], 8]");
                return LType{};
            }
            if (t.width <= 0) {
                error("'" + instr.name + ": " + t.kind + " capacity must be positive, got " +
                      std::to_string(t.width));
                return LType{};
            }
        }
        if (t.kind == "dict") {
            // 4.3. All three parts are required. A missing key type is the one
            // that matters most, because without it there is nothing to hash and
            // the table could not be laid out at all, so it must not degrade
            // into a value-only container that compares equal to something real.
            if (t.key_kind.empty() || t.elem_kind.empty()) {
                error("'" + instr.name + ": dict needs a key type and a value type, "
                      "e.g. dict[int[64], int[64], 8]");
                return LType{};
            }
            if (t.key_kind != "int" && t.key_kind != "bool") {
                error("'" + instr.name + ": dict keys are int or bool only, got " +
                      type_str(t.key()) + " (4.3)");
                return LType{};
            }
            // 4.3. A dict holds one scalar per slot, so a container in either
            // position is refused here. The frontend refuses it too, but the IR
            // is a surface of its own: `dict[int, list[...], 4]` typed nothing
            // before, and then the print guard reported a closed overload set
            // error about a value the user never wrote.
            if (t.elem_kind != "int" && t.elem_kind != "float" &&
                t.elem_kind != "bool") {
                error("'" + instr.name + ": dict values are int, float or bool only, got " +
                      type_str(t.element()) + " (4.3)");
                return LType{};
            }
            if (t.key_kind == "int" &&
                (t.key_width < 0 || t.key_width > 64 ||
                 (t.key_width % 8) != 0)) {
                error("'" + instr.name + ": dict int key width must be a multiple of 8 "
                      "up to 64, got " + std::to_string(t.key_width) + " (4.3)");
                return LType{};
            }
            if (t.width <= 0 || (t.width & (t.width - 1)) != 0) {
                // A power of two is what lets the bucket be a mask rather than a
                // division, so a count that is not one is rejected here instead
                // of producing a table whose lookups silently disagree.
                error("'" + instr.name + ": dict bucket count must be a positive "
                      "power of two, got " + std::to_string(t.width) + " (4.3)");
                return LType{};
            }
        }
        if (t.kind == "ptr" && t.elem_kind.empty()) {
            error("'" + instr.name + ": ptr needs an element type, e.g. ptr[int[64]]");
            return LType{};
        }
        if (t.kind == "ptr" && t.elem_kind != "int" && t.elem_kind != "float" &&
            t.elem_kind != "bool") {
            error("'" + instr.name + ": ptr pointees are int, float or bool only, got " +
                  type_str(t.element()) + " (4.4)");
            return LType{};
        }
        (void)container;
        return t;
    }

    void check_for_loop_shapes() {
        for (const auto& block : fn_.blocks) {
            for (size_t i = 0; i + 1 < block.instrs.size(); ++i) {
                const Instr& load_instr = block.instrs[i];
                const Instr& lt_instr = block.instrs[i + 1];
                if (load_instr.op != Op::Load || lt_instr.op != Op::Lt) continue;
                if (lt_instr.args.size() != 2 || lt_instr.args[0] != load_instr.result) continue;

                const std::string& loop_var = load_instr.name;
                auto scope_it = in_scope_by_block_.find(block.label);
                if (scope_it == in_scope_by_block_.end()) continue;
                auto type_it = scope_it->second.find(loop_var);
                if (type_it == scope_it->second.end() || type_it->second.kind != "int") continue;

                const Instr* bound_const = find_producing_const(lt_instr.args[1]);
                if (!bound_const) continue;

                int64_t n = bound_const->int_imm;
                auto [lo, hi] = int_range(type_it->second.width);
                int64_t max_produced = n - 1;
                if (max_produced > hi || lo > 0) {
                    error("for-loop: range(" + std::to_string(n) + ") produces values up to " +
                          std::to_string(max_produced) + ", which does not fit " +
                          type_str(type_it->second) + " (valid range " + std::to_string(lo) +
                          ".." + std::to_string(hi) + ") -- V1_SPEC 0.6.12");
                }
            }
        }
    }
};

} // namespace

std::vector<RCRError> check_module(const Module& module) {
    std::vector<RCRError> errors;

    // 4.3. A dict is rejected in parameter and return position explicitly.
    //
    // This is here rather than inherited because the existing behaviour for a
    // list or a tuple in that position is a silent pass, and copying it would
    // have been worse for a dict than useless. The IR function header keeps only
    // a param's kind and width, so both element types are already gone by the
    // time a type is built here, and a dict loses two of them. A param would
    // then look like a scalar of a nonsense width and pass the checker as
    // something it is not.
    //
    // Rejecting the kind outright is what stops that, and it costs one string
    // comparison. Nested containers are not covered by this, because they are
    // rejected where the element type is still known, in declared_type_of.
    //
    // 4.4. A ptr is rejected in those positions for the same structural reason:
    // the header keeps the pointee's kind and width nowhere, so a passed
    // pointer would lose what it points at and degrade into a bare address.
    // Pointers also carry no runtime meaning across the sysv ABI's two register
    // banks, which the marshalling code has no way to distinguish.
    for (const auto& fn : module.functions) {
        for (size_t i = 0; i < fn.param_type_kinds.size(); ++i) {
            if (fn.param_type_kinds[i] == "dict") {
                errors.push_back({fn.name + ": parameter '" + fn.params[i] +
                                  "' cannot be a dict (4.3); a dict is a frame of "
                                  "buckets and is not passed by value"});
            }
            if (fn.param_type_kinds[i] == "ptr") {
                errors.push_back({fn.name + ": parameter '" + fn.params[i] +
                                  "' cannot be a ptr (4.4); a pointer's pointee "
                                  "does not survive the function header, so "
                                  "pointers are not passed by value"});
            }
        }
        if (fn.return_type_kind == "dict") {
            errors.push_back({fn.name + ": return type cannot be a dict (4.3); "
                              "returning a container is not part of 4.3"});
        }
        if (fn.return_type_kind == "ptr") {
            errors.push_back({fn.name + ": return type cannot be a ptr (4.4); "
                              "a pointer's pointee does not survive the function "
                              "header, so returning one is not part of 4.4"});
        }
    }

    std::unordered_map<std::string, FnMeta> all_fns;
    for (const auto& fn : module.functions) {
        FnMeta meta;
        for (size_t i = 0; i < fn.params.size(); ++i) {
            meta.param_types.push_back(LType{fn.param_type_kinds[i], fn.param_type_widths[i]});
        }
        meta.has_return_type = !fn.return_type_kind.empty();
        if (meta.has_return_type) {
            meta.return_type = LType{fn.return_type_kind, fn.return_type_width};
        }
        all_fns[fn.name] = meta;
    }

    for (const auto& fn : module.functions) {
        FunctionChecker checker(module, fn, errors, all_fns);
        checker.run();
    }
    return errors;
}

} // namespace lithon::typecheck
