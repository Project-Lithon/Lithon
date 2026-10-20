#include "typecheck.h"
#include "../dict_hash.h"
#include "../jit/float_runtime.h"
#include <unordered_map>
#include <unordered_set>
#include <cstdint>
#include <algorithm>
#include <limits>
#include <memory>
#include <string>

namespace lithon::typecheck {

using namespace lithon::ir;

namespace {

// 4.1. A type is recursive: `list[int[64], 10]` is a kind, a capacity, and an
// ELEMENT type, and the element may itself be a container. The full shape is
// held as a shared_ptr tree in elem_tree/key_tree. The flat elem_kind/elem_width
// (and key) are kept in parallel: they are the SAME one-level projection the
// IR carries (`Instr::type_elem_kind`/...), so every pre-nesting reader of a
// type still works unchanged. When a tree is present, element()/key() return
// it; otherwise they fall back to the flat pair.
//
// The element MUST participate in equality. Without it `list[int[64],10]` and
// `list[float[64],10]` would compare equal, and assigning one to the other
// would pass the checker -- which is exactly the class of bug a typed container
// is supposed to make impossible. Equality walks the tree, so a nested element
// is compared to its full depth rather than to a flattened approximation.
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

    // The full element/key types. Null for a scalar, and also for a one-level
    // container built from flat IR fields only (then the flat pair is the
    // element). Set when built from the recursive `ir::Type` decl_type.
    std::shared_ptr<LType> elem_tree;
    std::shared_ptr<LType> key_tree;

    bool has_elem() const { return elem_tree != nullptr || !elem_kind.empty(); }
    bool has_key() const { return key_tree != nullptr || !key_kind.empty(); }

    bool operator==(const LType& other) const {
        if (kind != other.kind || width != other.width) return false;
        if (has_elem() != other.has_elem()) return false;
        if (has_elem() && !(element() == other.element())) return false;
        if (has_key() != other.has_key()) return false;
        if (has_key() && !(key() == other.key())) return false;
        return true;
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
    //
    // 4.6. A struct is deliberately NOT a scalar even though it carries no
    // element or key: `width` is a byte size, not a bit width, so int_range()
    // must never see it, and a struct has no single cell that addressof or a
    // value store could name. Keeping it out of is_scalar() is what routes a
    // valueless struct store down the same "binds storage" path a container
    // takes rather than the type-only scalar path.
    bool is_scalar() const {
        return !has_elem() && !has_key() && kind != "struct";
    }
    bool is_list() const { return kind == "list"; }
    bool is_dict() const { return kind == "dict"; }
    bool is_ptr() const { return kind == "ptr"; }
    bool is_struct() const { return kind == "struct"; }

    // 4.4. The element stride a pointer offset is scaled by, in BYTES. The
    // frontend lowers `_p + 1` to `add _p, sizeof(T)`, so an offset is a byte
    // count and this is what both it and the checker multiply/divide by. A
    // bool and an int[8] both pack to one byte.
    int byte_stride() const {
        if (elem_kind == "bool") return 1;
        if (elem_width >= 8) return elem_width / 8;
        return 1;
    }

    // The element type of a container, as a standalone LType. Returns the full
    // recursive child when one is known, so `xs[i]` on a list of tuples is
    // itself a tuple and can be indexed again.
    LType element() const {
        if (elem_tree) return *elem_tree;
        return LType{elem_kind, elem_width};
    }
    LType key() const {
        if (key_tree) return *key_tree;
        return LType{key_kind, key_width};
    }
};

// Builds the recursive LType from the IR's recursive type tree. The flat fields
// are projected the same way `parse_type_string` projects them into the Instr,
// so a type built here equals one built from flat fields alone.
LType from_ir_type(const Type& t) {
    LType out;
    out.kind = t.kind;
    // 6.2. A str[N]'s `width` is its byte capacity N and its element is the
    // byte int[8], exactly as a container's width is its capacity and its
    // element is the declared one. Keeping it out of is_container() (it prints
    // and lengths as a string) is why it needs this explicit projection.
    if (t.kind == "str") {
        out.width = t.cap;
        if (t.elem) {
            out.elem_tree = std::make_shared<LType>(from_ir_type(*t.elem));
            out.elem_kind = t.elem->kind;
            // 6.8. The str's own ELEMENT is the byte, int[8], and that is what
            // the projection above reads -- but a str used AS an element or a
            // key of a container (a list[str[8], 4], a dict[str[16], ...]) has
            // its capacity in `cap`, exactly as a list's own element type does.
            // Reading `width` there returned -1 and the print guard called it
            // "not provably a str", which refused the native tier on a str
            // element it had just been told about.
            out.elem_width = t.elem->is_container() ? t.elem->cap : t.elem->width;
        }
        return out;
    }
    out.width = t.is_container() ? t.cap : t.width;
    if (t.elem && (t.is_container() || t.kind == "ptr")) {
        out.elem_tree = std::make_shared<LType>(from_ir_type(*t.elem));
        out.elem_kind = t.elem->kind;
        out.elem_width = t.elem->kind == "str"
                             ? t.elem->cap
                             : (t.elem->is_container() ? t.elem->cap : t.elem->width);
    }
    if (t.kind == "dict" && t.key) {
        out.key_tree = std::make_shared<LType>(from_ir_type(*t.key));
        out.key_kind = t.key->kind;
        // 6.8. A str key's width is its BYTE CAPACITY, not a bit width, exactly
        // as a str variable's is -- and exactly as a container key's is its
        // bucket count. `is_container()` deliberately excludes str, so without
        // this the capacity fell through to `t.key->width`, which a str never
        // has, and every str-keyed dict read back as a key with no capacity at
        // all.
        out.key_width = t.key->kind == "str"
                            ? t.key->cap
                            : (t.key->is_container() ? t.key->cap : t.key->width);
        // 6.8. A str VALUE reads its capacity the same way a str key does, for
        // the same reason: a dict's element type is a container's element type
        // in every respect except that the capacity lives in `cap`.
        if (t.elem && t.elem->kind == "str") out.elem_width = t.elem->cap;
    }
    return out;
}

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
    // 4.6. A struct's width is a byte size and it is never a scalar, so it must
    // be rendered before the scalar branch (which would print it as a width)
    // and before the container branch (which would invent a phantom element).
    if (t.is_struct()) return "struct[" + std::to_string(t.width) + "]";
    // 6.2. A str prints as str[N], never as the implicit byte element, because
    // the source never spells the element and a diagnostic must quote the
    // source. Place it before the scalar branch (is_scalar() is false for a str,
    // whose width is a byte capacity) and before the container branch.
    if (t.kind == "str") return "str[" + std::to_string(t.width) + "]";
    if (t.is_scalar()) {
        if (t.width < 0) return t.kind;
        return t.kind + "[" + std::to_string(t.width) + "]";
    }
    // 4.1. Render a container back the way it was written, so a diagnostic
    // quotes the source spelling rather than a flattened approximation. When the
    // full nested shape is known it is rendered to its depth; otherwise a
    // one-level container element is rendered `list[...]` (printing its capacity
    // alone, `list[4]`, would read like a list of ints named 4).
    auto render_elem = [&]() -> std::string {
        if (t.elem_tree) return type_str(t.element());
        if (t.elem_kind.empty()) return "?";
        if (t.elem_kind == "list" || t.elem_kind == "tuple" || t.elem_kind == "ptr")
            return t.elem_kind + "[...]";
        std::string s = t.elem_kind;
        if (t.elem_width >= 0) s += "[" + std::to_string(t.elem_width) + "]";
        return s;
    };
    // 4.3. A dict prints as it was written, key first, because that is the
    // order it was declared in.
    if (t.is_dict()) {
        std::string k = t.key_kind.empty() ? "?" : t.key_kind;
        if (t.key_width >= 0) k += "[" + std::to_string(t.key_width) + "]";
        return t.kind + "[" + k + ", " + render_elem() + ", " + std::to_string(t.width) + "]";
    }
    // ptr carries no capacity and must not print a phantom ", -1".
    if (t.kind == "ptr") return t.kind + "[" + render_elem() + "]";
    return t.kind + "[" + render_elem() + ", " + std::to_string(t.width) + "]";
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
        bool header_invalid = false;
        for (size_t i = 0; i < fn_.params.size(); ++i) {
            if (fn_.param_type_kinds[i].empty()) {
                error("function '" + fn_.name + "': parameter '" + fn_.params[i] +
                      "' has no type annotation (V1_SPEC 0.6.1, 0.6.8)");
                header_invalid = true;
                continue;
            }
            entry_scope[fn_.params[i]] = LType{fn_.param_type_kinds[i], fn_.param_type_widths[i]};
            check_scalar_width(fn_.param_type_kinds[i], fn_.param_type_widths[i]);
        }
        // Entry points are exempt from the return-annotation rule: the frontend's
        // implicit top-level function is __main__, and hand-written IR may use main.
        if (fn_.name != "main" && fn_.name != "__main__" && !has_return_type) {
            error("function '" + fn_.name + "' has no return type annotation (V1_SPEC 0.6.8)");
            header_invalid = true;
        }

        // A header with a missing annotation is already the one error worth
        // reporting. Walking the body after it produces only consequences of
        // that error -- an unannotated parameter is not in the entry scope, so
        // each read of it becomes a spurious "not definitely assigned", and a
        // call that returns the still-untyped value grows more of the same.
        // Stop here so the user sees exactly the root cause.
        if (header_invalid) return;

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

    // E0105, recursive form: a nested element or dict key/value can be an
    // unsupported int/float width just as a one-level one can, and the flat
    // instr fields only reach the first level. Walks the IR tree so the check
    // sees every scalar in the declaration.
    void check_scalar_width_type(const Type& t) {
        check_scalar_width(t.kind, t.is_container() ? -1 : t.width);
        if (t.elem) check_scalar_width_type(*t.elem);
        if (t.key) check_scalar_width_type(*t.key);
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
                } else if (instr.op == Op::TryEnter) {
                    // 6.10. A handler block is entered by a trap jump, not a
                    // `jump`, so without this edge it would look predecessorless
                    // and start from the entry scope -- losing every variable
                    // assigned before the try. The edge is from the TryEnter's
                    // own block, so a handler's in-scope is the try-entry scope,
                    // which is exactly what is definitely assigned when a trap
                    // fires anywhere in the body.
                    for (const auto& label : lithon::ir::split_marks(instr.name2)) {
                        add_edge(block.label, label);
                    }
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

    // 6.3 Stage A. The bounded run a printed string literal / f-string chunk is
    // materialised into before it is printed. Kept in sync with PRINT_LIT_TEMP
    // in src/frontend/frontend.py. Like the merge temp it is compiler-generated
    // and starts with '_', so it must be exempt from 4.4's pointer-name rule --
    // it is a `str[N]`, not a pointer.
    static bool is_print_literal_temp(const std::string& name) {
        return name.rfind("__printlit", 0) == 0;
    }

    // 6.9. The run a dict's container VALUE is built into before the store copies
    // it into the value slot. Kept in sync with DICT_VALUE_TEMP in
    // src/frontend/frontend.py; compiler-generated like the two above, so it is
    // exempt from 4.4's pointer-name rule.
    static bool is_dict_value_temp(const std::string& name) {
        return name.rfind("__dictval", 0) == 0;
    }

    // A name the frontend mints for its own lowering (never a source
    // identifier). Exempt from the naming rule, which exists to police what the
    // programmer writes, not what the compiler introduces.
    static bool is_compiler_temp(const std::string& name) {
        return is_lowered_merge_temp(name) || is_print_literal_temp(name) ||
               is_dict_value_temp(name);
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
                    // exactly one store per LEAF. For a flat tuple that is its
                    // capacity, but a nested tuple literal writes every leaf
                    // through one IndexStore, so the count is the flat-leaf
                    // count (a `tuple[tuple[int[64],2],2]` opens for 4 stores,
                    // not 2). Re-declaring the same name restarts the count.
                    int open_count = instr.type_width;
                    if (instr.decl_type) {
                        const auto layout = lithon::ir::container_layout(*instr.decl_type);
                        if (layout.valid) open_count = layout.leaf_count;
                    }
                    open_init[instr.name] = {open_count, &instr};
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
                    if (kit != open_dict_keys.end()) {
                        bool dict_str_key = false;
                        if (auto dit = scope.find(instr.name); dit != scope.end())
                            dict_str_key = dit->second.key_kind == "str";
                        if (dict_str_key) {
                            // 4.3. A str key arrives as a NAMED run, and the
                            // frontend shares one materialised run between every
                            // literal of the same capacity -- so the name
                            // carries neither the key's text nor a constant to
                            // compare, and a run name would see two DIFFERENT
                            // keys of the same length as one duplicate. The
                            // frontend has the literals and has already refused a
                            // duplicate string key, so only the entry count is
                            // bounded here.
                            std::vector<int64_t>& seen = kit->second;
                            seen.push_back(-static_cast<int64_t>(seen.size()) - 1);
                            int64_t buckets = 0;
                            if (auto dit = scope.find(instr.name); dit != scope.end())
                                buckets = dit->second.width;
                            if (int64_t(seen.size()) > buckets) {
                                error("'" + instr.name + "' has " +
                                      std::to_string(seen.size()) + " entries but only " +
                                      std::to_string(buckets) + " buckets (4.3)");
                            }
                        } else if (!instr.args.empty()) {
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
                    }
                    continue;
                }
                // Any other mention of the name ends construction. That covers
                // reading it, loading it, passing it, and assigning to it whole.
                const bool mentions = instr.op == Op::Index ||
                                      instr.op == Op::DictIndex ||
                                      instr.op == Op::DictContains ||
                                      // 6.1. Printing a container reads it, so it
                                      // ends construction exactly as a load does.
                                      instr.op == Op::PrintContainer ||
                                      instr.op == Op::Load ||
                                      instr.op == Op::Call ||
                                      (instr.op == Op::Store && !instr.args.empty());
                if (!mentions) continue;
                auto it = open_init.find(instr.name);
                if (it == open_init.end()) continue;
                const OpenInit& st = it->second;
                int64_t capacity = st.decl->type_width;
                // 4.1 (phase 3). A nested tuple literal writes every LEAF, so a
                // partial one is measured against the flat-leaf count, not the
                // outer capacity.
                if (st.decl->decl_type) {
                    const auto layout =
                        lithon::ir::container_layout(*st.decl->decl_type);
                    if (layout.valid) capacity = layout.leaf_count;
                }
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
            // 4.1-4.3. Two containers of the same kind agree only if their
            // element -- and, for a dict, key -- type matches too. Comparing
            // capacity alone accepted `list[int[64],4]` into `list[float[64],4]`
            // and `list[int[8],4]` into `list[int[64],4]`, which is exactly the
            // class of mismatch a typed container exists to make impossible.
            if (source.kind == "list" || source.kind == "tuple" || source.kind == "dict") {
                const bool same_elem = source.element() == target.element();
                const bool same_key =
                    source.kind != "dict" || source.key() == target.key();
                if (source.width != target.width || !same_elem || !same_key) {
                    error(context + ": cannot convert " + type_str(source) + " to " +
                          type_str(target) + " -- a " + source.kind +
                          "'s element type must match (4.1)");
                    return;
                }
                return;
            }
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

    // 6.9. The NAMED form of the check above, for a dict value that is a
    // container: the value arrives as the name of a run rather than as a
    // register, so `find_producing_const` has nothing to look at and the literal
    // checks above do not apply. What has to hold is that the named run was
    // declared (it must be assigned, like a str key run) and that its type IS the
    // declared value type -- a run of `list[int[32], 4]` cannot go into a slot
    // the table sized for `list[int[64], 4]`.
    void check_value_into_target_named(const std::string& name, const LType& target,
                                       const Scope& scope,
                                       const std::string& context) {
        auto it = scope.find(name);
        if (it == scope.end()) {
            error(context + ": '" + name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
            return;
        }
        if (!(it->second == target)) {
            error(context + ": '" + name + "' is " + type_str(it->second) +
                  ", which is not " + type_str(target));
        }
    }

    void check_value_into_target(ValueId id, const LType& target, const std::string& context) {
        // 4.6. A struct has no value: it is storage, written one field at a
        // time. This is the backstop for hand-written IR, where a value store
        // into a struct-typed variable would otherwise be silently accepted
        // (and then only move the first word of the blob natively). The
        // frontend never emits such a store.
        if (target.is_struct()) {
            error(context + ": a struct has no value -- its fields are assigned one "
                  "at a time (4.6)");
            return;
        }
        if (const Instr* c = find_producing_const(id)) {
            if (target.kind == "int") {
                // A bool literal is NOT an int literal. `True` carries
                // int_imm == 1, so the range check below used to wave it
                // straight through and return, and `v: int[64] = True` typed
                // clean: the declaration became a contract about nothing, the
                // register stayed bool-tagged, and both tiers happily printed
                // True out of a name declared int. It never reached
                // check_assignment_compatible, because this branch returns
                // before it. Reject it here, where the constant is in hand.
                if (c->op == Op::ConstBool) {
                    error(context + ": " + std::string(c->int_imm ? "True" : "False") +
                          " is a bool, but " + type_str(target) +
                          " must hold an int -- Lithon does not convert bool to int on "
                          "assignment; write 1 or 0 (V1_SPEC 0.6.11)");
                    return;
                }
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
        // 6.1. print() takes any number of arguments; each is formatted
        // independently, so each must be in the closed printable set. The
        // frontend lowers an n-argument print to n print_bare/print calls plus
        // separators, so a single call still carries exactly one value -- this
        // loop is what keeps hand-written IR honest, and it accepts the
        // zero-argument (bare-newline) case too.
        // 4.4. ptr[T] prints as a hexadecimal address (0x7ffd...), the way C's
        // %p and Rust's {:p} do. It is an address, not the pointee: print(p)
        // shows where, print(valueof(p)) shows what.
        static const std::unordered_set<std::string> allowed = {"int", "float", "str", "bool", "ptr"};
        for (ValueId id : instr.args) {
            LType t;
            if (!reg_type(id, t)) continue;
            if (allowed.find(t.kind) == allowed.end()) {
                error("print() does not accept " + type_str(t) + " -- V1_SPEC 0.6.9's closed "
                      "overload set is int[N], float[N], str[N], bool and ptr[T] only");
            }
        }
    }

    // 6.1. PrintContainer names a container VARIABLE and carries its shape in
    // the trailing type suffix. The name must be a live list/tuple/dict and
    // every leaf it reaches must be a printable scalar -- the same closed set the
    // scalar print family accepts, because a container is printed by rendering
    // each of its leaves. The suffix is also checked against the declared type,
    // since both engines lay out from the declaration but render from the suffix.
    void check_print_container(const Instr& instr, const Scope& scope) {
        auto it = scope.find(instr.name);
        if (it == scope.end()) {
            error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
            return;
        }
        const LType& ct = it->second;
        if (ct.kind != "list" && ct.kind != "tuple" && !ct.is_dict() &&
            ct.kind != "str") {
            error("print() of " + type_str(ct) + " -- only a list, tuple or dict "
                  "prints as an aggregate (6.1)");
            return;
        }
        // Phase 6.7. A list names its live-length companion as name2; it must be
        // a live int scalar (it is read as the element count at run time). A
        // tuple, dict or str carries none.
        if (!instr.name2.empty()) {
            if (ct.kind != "list") {
                error("print_container " + instr.name + " carries a length variable but " +
                      instr.name + " is " + type_str(ct) + ", which has no length field (6.7)");
                return;
            }
            auto lit = scope.find(instr.name2);
            if (lit == scope.end()) {
                error("'" + instr.name2 + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                return;
            }
            if (lit->second.kind != "int") {
                error("print_container " + instr.name + "'s length variable " + instr.name2 +
                      " is " + type_str(lit->second) + ", not an int (6.7)");
                return;
            }
        }
        if (ct.kind != instr.type_kind || ct.width != instr.type_width) {
            error("print_container " + instr.name + " carries " + instr.type_kind +
                  "[...," + std::to_string(instr.type_width) + "] but " + instr.name +
                  " is " + type_str(ct) + " (6.1)");
            return;
        }
        // 6.2. A top-level str prints its bytes as text, not as an aggregate of
        // bytes. When a str is instead an ELEMENT (`list[str[8], N]`) it is a
        // printable leaf: both engines render it with the Python repr, quoted,
        // exactly as Python prints a list of strings. So str is added to the
        // printable leaf set below, and the top-level case returns here.
        if (ct.kind == "str") return;
        const auto leaf_ok = [](const std::string& k) {
            return k == "int" || k == "float" || k == "bool" || k == "str";
        };
        if (ct.is_dict()) {
            if (ct.key_kind != instr.type_key_kind ||
                ct.key_width != instr.type_key_width) {
                error("print_container " + instr.name + " key type does not match " +
                      type_str(ct) + " (6.1)");
                return;
            }
            if (!leaf_ok(ct.key_kind)) {
                error("print() of " + type_str(ct) + " -- a dict key of type " +
                      type_str(ct.key()) + " is not printable (6.1)");
            }
            if (ct.elem_kind != instr.type_elem_kind ||
                ct.elem_width != instr.type_elem_width) {
                error("print_container " + instr.name + " element type does not match " +
                      type_str(ct) + " (6.1)");
                return;
            }
            // 6.9. A dict VALUE may itself be a list or a tuple (4.3). It is
            // printable under the same rule a list element is: descend to the
            // deepest leaf and require it to be in the closed set. A dict value
            // can never be a dict (that is refused at the declaration), so the
            // walk is bounded by the value type itself.
            LType vleaf = ct.element();
            while (vleaf.kind == "list" || vleaf.kind == "tuple")
                vleaf = vleaf.element();
            if (!leaf_ok(vleaf.kind)) {
                error("print() of " + type_str(ct) + " -- an element of type " +
                      type_str(ct.element()) + " is not printable (6.1)");
            }
            return;
        }
        // 4.1 (phase 3). A nested list/tuple is printable iff its LEAF is in the
        // closed set; the intermediate levels are containers and have no scalar
        // kind of their own, so the walk descends to the deepest element.
        LType leaf = ct;
        while (leaf.kind == "list" || leaf.kind == "tuple")
            leaf = leaf.element();  // depth-bounded by the type itself
        if (!leaf_ok(leaf.kind)) {
            error("print() of " + type_str(ct) + " -- a leaf of type " +
                  type_str(leaf) + " is not printable (6.1)");
            return;
        }
        // Validate the WHOLE recursive shape the suffix carries, not just the
        // immediate element: both engines lay out and render from the suffix, so
        // a suffix that disagrees at any depth (`list[list[float[64],2],3]` over a
        // declared `list[list[int[64],2],3]`) would silently re-read bytes.
        const auto shape_matches = [](const LType& a, const Type& b) {
            auto self = [](const LType& x, const Type& y, auto&& rec) -> bool {
                if (x.kind != y.kind) return false;
                if (x.kind == "list" || x.kind == "tuple") {
                    if (x.width != y.cap || !y.elem) return false;
                    return rec(x.element(), *y.elem, rec);
                }
                if (x.kind == "bool") return true;
                // 6.2. A str's byte capacity rides in the IR `cap`, not `width`
                // (its width is never a bit width), so it matches on cap.
                if (x.kind == "str") return y.elem && x.width == y.cap;
                return x.width == y.width;
            };
            return self(a, b, self);
        };
        if (instr.decl_type) {
            if (!shape_matches(ct, *instr.decl_type)) {
                error("print_container " + instr.name + " shape does not match " +
                      type_str(ct) + " (6.1)");
            }
            return;
        }
        // Hand-written IR with no recursive suffix: fall back to the flat check.
        if (ct.elem_kind != instr.type_elem_kind ||
            ct.elem_width != instr.type_elem_width) {
            error("print_container " + instr.name + " element type does not match " +
                  type_str(ct) + " (6.1)");
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
            // A callee parameter with no annotation is the callee's own header
            // error, already reported there. Type-arguing the call against an
            // empty type would only echo it as a nameless "cannot convert to "
            // -- the cascade this skip exists to stop.
            if (callee.param_types[i].kind.empty()) continue;
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
        if (instr.decl_type) {
            check_scalar_width_type(*instr.decl_type);
        } else {
            check_scalar_width(instr.type_kind, instr.type_width);
            check_scalar_width(instr.type_elem_kind, instr.type_elem_width);
            check_scalar_width(instr.type_key_kind, instr.type_key_width);
        }
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
                if (!t.is_scalar() && !t.is_struct() && t.kind != "str") {
                    error("cannot take the address of " + type_str(t) +
                          " -- addressof takes a plain scalar variable or a struct (4.4)");
                    return;
                }
                // 6.2. A bounded string is real storage -- an N-byte run -- so it
                // can be pointed at like a struct. The pointer must carry the
                // WHOLE str element (its recursive shape included) so it equals
                // the `ptr[str[N]]` the frontend declares; a flat-only element
                // would compare unequal to a parsed one and be rejected here.
                if (t.kind == "str") {
                    LType p;
                    p.kind = "ptr";
                    p.width = -1;
                    p.elem_kind = "str";
                    p.elem_width = t.width;
                    p.elem_tree = std::make_shared<LType>(t);
                    reg_types_[instr.result] = p;
                    return;
                }
                // A struct's width IS its byte size, so ptr[struct[N]] carries
                // the stride that pointer arithmetic scales by.
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
            // 4.6. Struct field access. The runtime validates nothing (there is
            // no field table in the IR): the check here is the whole guarantee
            // that a field op addresses a real byte range of the struct, so the
            // offset must be a compile-time constant inside the declared size
            // and the field must be a scalar whose width fits from that offset.
            case Op::FieldLoad:
            case Op::FieldStore: {
                // 4.4, second half. A field op whose subject starts with '%'
                // goes through a POINTER: the last operand is the base, which
                // must be a ptr[struct[N]], and the offset is bounded by that
                // struct's size rather than by any named variable's. The named
                // form below is unchanged.
                if (instr.name.empty() && !instr.args.empty()) {
                    const size_t base_arg =
                        instr.op == Op::FieldStore ? instr.args.size() - 1 : 0;
                    LType bt;
                    if (!reg_type(instr.args[base_arg], bt)) {
                        error("field access through a base that is unresolved (4.4)");
                        return;
                    }
                    if (!bt.is_ptr() || bt.element().kind != "struct") {
                        error("field access through " + type_str(bt) +
                              " -- the base of a pointer-based field is a "
                              "ptr[struct], e.g. addressof(p) (4.4)");
                        return;
                    }
                    const int64_t poff = instr.int_imm;
                    if (poff < 0 || poff >= bt.element().width) {
                        error("field offset " + std::to_string(poff) + " is out of range for " +
                              type_str(bt.element()) + " (4.4, 4.6)");
                        return;
                    }
                    return;
                }
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                const LType& st = it->second;
                if (!st.is_struct()) {
                    error(instr.name + " is " + type_str(st) +
                          ", not a struct -- a field is read as s.field, so this "
                          "instruction names a struct variable (4.6)");
                    return;
                }
                const int64_t off = instr.int_imm;
                if (off < 0 || off >= st.width) {
                    error("field offset " + std::to_string(off) + " is out of range for " +
                          type_str(st) + " -- valid offsets are 0.." +
                          std::to_string(st.width - 1) + " (4.6)");
                    return;
                }
                const LType field{instr.type_kind, instr.type_width, "", -1, "", -1};
                if (field.kind != "int" && field.kind != "float" && field.kind != "bool") {
                    error("a struct field is a scalar, got " +
                          (field.kind.empty() ? std::string("an untyped field")
                                              : type_str(field)) + " (4.6)");
                    return;
                }
                // The load/store width is the field's own size, so the access
                // must lie entirely inside the struct. The frontend lays fields
                // out so this always holds; the checker is the backstop for a
                // hand-written offset that would run past the blob.
                int field_bytes = 1;
                if (field.kind == "int" || field.kind == "float") field_bytes = field.width / 8;
                if (off + field_bytes > st.width) {
                    error("field at offset " + std::to_string(off) + " with type " +
                          type_str(field) + " overruns " + type_str(st) + " (4.6)");
                    return;
                }
                if (instr.op == Op::FieldLoad) {
                    reg_types_[instr.result] = field;
                } else {
                    if (instr.args.empty()) {
                        error("FieldStore needs a value");
                        return;
                    }
                    check_value_into_target(instr.args.at(0), field,
                                            "store into field of '" + instr.name + "'");
                }
                return;
            }
            // Hardening item 5. Explicit numeric conversions (V1_SPEC 0.6.11).
            //
            // float(64, v) widens an int[8|16|32] EXACTLY to float[64] (every
            // such integer has at most 32 significant bits, far below the 53 a
            // double represents) and passes a float[64] through unchanged. An
            // int[64] is refused as lossy (E0106): a 64-bit integer can need 64
            // significant bits and the conversion would silently round. There
            // is no float[N] other than 64.
            case Op::ToFloat: {
                LType src;
                if (!reg_type(instr.args.at(0), src)) {
                    error("float(...) needs an int or float value, got an unresolved value");
                    return;
                }
                if (instr.type_kind != "float" || instr.type_width != 64) {
                    error("float(...) only produces float[64], got " +
                          type_str(LType{instr.type_kind, instr.type_width}));
                    return;
                }
                if (src.kind == "float") {
                    if (src.width != 64) {
                        error("float(...) of " + type_str(src) + " -- only float[64] exists");
                        return;
                    }
                } else if (src.kind == "int") {
                    if (src.width == 64) {
                        error("LITHON-E0106: float(64, ...) of " + type_str(src) +
                              " is refused -- a double has 53 significant bits and cannot "
                              "hold every int[64] exactly; convert an int[8|16|32] or keep "
                              "it an int");
                        return;
                    }
                    if (src.width != 8 && src.width != 16 && src.width != 32) {
                        error("float(...) of " + type_str(src) + " -- only int[8|16|32] widen");
                        return;
                    }
                } else {
                    error("float(...) takes an int or a float, got " + type_str(src));
                    return;
                }
                reg_types_[instr.result] = LType{"float", 64};
                return;
            }
            // int(w, v) truncates a float[64] toward zero into int[w]. This is
            // the ONLY conversion into an int: there is no int -> int narrowing,
            // so an int operand is refused here with a message that says so.
            case Op::ToInt: {
                LType src;
                if (!reg_type(instr.args.at(0), src)) {
                    error("int(...) needs a float value, got an unresolved value");
                    return;
                }
                if (src.kind != "float" || src.width != 64) {
                    error("int(...) takes a float[64], got " + type_str(src) +
                          " -- there is no int-to-int narrowing; float -> int is the "
                          "only conversion in this direction (V1_SPEC 0.6.11)");
                    return;
                }
                if (instr.type_kind != "int" || instr.type_width < 0) {
                    error("int(...) needs an explicit target width, e.g. int[64]");
                    return;
                }
                reg_types_[instr.result] = LType{"int", instr.type_width};
                return;
            }
            // int(w, input(prompt)): reads one line of stdin and parses
            // int[w]. The width was validated by check_scalar_width above.
            case Op::InputInt: {
                if (instr.type_kind != "int" || instr.type_width < 0) {
                    error("input() needs an explicit target width, e.g. int[64]");
                    return;
                }
                reg_types_[instr.result] = LType{"int", instr.type_width};
                return;
            }
            case Op::GuardNonZero: {
                if (instr.args.empty()) {
                    error("guardnonzero needs an argument");
                    return;
                }
                LType st;
                if (!reg_type(instr.args.at(0), st)) {
                    error("guardnonzero needs an int value, got unresolved");
                    return;
                }
                if (st.kind != "int") {
                    error("guardnonzero needs an int value, got " + type_str(st));
                    return;
                }
                return;
            }

            // Phase III. The raw syscall primitive. `args` is the number
            // followed by up to six arguments; each argument is an int[64] or a
            // pointer. 5.2 needs pointers: `bind`/`connect` take `&sockaddr_in`
            // and `send`/`recv` take a buffer, all `ptr[struct[N]]` into real
            // frame memory. A pointer is a real host address in BOTH tiers
            // (4.4), so the kernel can read and write the bytes behind it -- the
            // address itself is never compared across tiers, only the bytes it
            // names. The result is the kernel's raw int64 return, unchecked: no
            // fallible wrapper and no use-before-check rule in this slice, so it
            // types as int[64].
            case Op::Syscall: {
                if (instr.args.empty()) {
                    error("syscall() needs a syscall number");
                    return;
                }
                if (instr.args.size() > 7) {
                    error("syscall() takes a number and at most six arguments, got " +
                          std::to_string(instr.args.size() - 1));
                    return;
                }
                for (size_t i = 0; i < instr.args.size(); ++i) {
                    LType at;
                    if (!reg_type(instr.args.at(i), at)) {
                        error("syscall() argument " + std::to_string(i) +
                              " is unresolved");
                        return;
                    }
                    if (at.kind != "int" && at.kind != "ptr") {
                        error("syscall() argument " + std::to_string(i) + " has type " +
                              type_str(at) + " -- a raw syscall argument must be an "
                              "integer or a pointer (a buffer or a sockaddr); floats "
                              "and aggregates are not passed by value (Phase III)");
                        return;
                    }
                }
                reg_types_[instr.result] = LType{"int", 64};
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
                // 6.2. A str is indexable like a one-level container (its bytes),
                // so it is admitted here alongside list and tuple. It is NOT a
                // tuple, so the immutability store-refusal below never applies:
                // `s[i] = byte` is the whole point of a mutable string.
                if (ct.kind != "list" && ct.kind != "tuple" && ct.kind != "str") {
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
                    // 4.6. A struct is neither a list nor a dict, but its field
                    // access is name-based, so index/len is a different error
                    // from "not a container" and worth naming concretely.
                    if (ct.is_struct()) {
                        error(instr.name + " is " + type_str(ct) +
                              " -- a struct's fields are read by name (`" +
                              instr.name + ".field`), not by index or len() (4.6)");
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
                    // 6.2. A string's len() is run-time, so the frontend emits
                    // StrLen for it. Reaching Len with a str name would fold to
                    // the capacity N and silently report the wrong thing, so it
                    // is refused and the right opcode named.
                    if (ct.kind == "str") {
                        error("len() on " + type_str(ct) + " is run-time and lowers "
                              "to strlen, not Len (6.2)");
                        return;
                    }
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

                // 4.1 (phase 3). A nested access carries the WHOLE index chain:
                // `xs[i][j]` is one instruction. Every argument but the last is
                // an index (for IndexStore the last is the value). Walk the
                // chain down the container, each level must be indexable, and
                // the walk must land on a scalar leaf -- a container intermediate
                // has no value to read or write (Option A: only the outermost
                // kind decides tuple immutability, which was checked above).
                const bool is_store = instr.op == Op::IndexStore;
                if (instr.args.empty() || (is_store && instr.args.size() < 2)) {
                    error("IndexStore needs at least an index and a value");
                    return;
                }
                const size_t n_idx = is_store ? instr.args.size() - 1 : instr.args.size();
                LType cur = ct;
                for (size_t k = 0; k < n_idx; ++k) {
                    // 6.2. A str is indexable as its bytes, so it is a valid
                    // level here just like list and tuple; its `width` is N, the
                    // bound the range check below uses.
                    if (cur.kind != "list" && cur.kind != "tuple" && cur.kind != "str") {
                        error("index " + std::to_string(k + 1) + " into " +
                              type_str(ct) + " goes past its innermost level: " +
                              type_str(cur) + " is not a list or tuple");
                        return;
                    }
                    const ValueId idx = instr.args.at(k);
                    LType idx_t;
                    if (reg_type(idx, idx_t) && idx_t.kind != "int") {
                        error("index into " + type_str(cur) + " must be an int, got " +
                              type_str(idx_t));
                        return;
                    }
                    // Static overflow. Only a LITERAL index is decidable here; a
                    // runtime index is checked in codegen, not guessed at here.
                    if (const Instr* c = find_producing_const(idx)) {
                        const int64_t n = c->int_imm;
                        if (n < 0 || n >= cur.width) {
                            error("index " + std::to_string(n) + " is out of range for " +
                                  type_str(cur) + " -- valid indices are 0.." +
                                  std::to_string(cur.width - 1));
                            return;
                        }
                    }
                    cur = cur.element();
                }
                const bool cur_is_container =
                    cur.kind == "list" || cur.kind == "tuple";
                if (instr.op == Op::Index) {
                    if (cur_is_container) {
                        error("reading " + type_str(cur) + " out of " + instr.name +
                              " would need a container value, which the language does "
                              "not have -- index all the way to a scalar (4.1)");
                        return;
                    }
                    // 6.2. A str is storage too: reading a whole one would need a
                    // str value, which the language does not have. Index all the
                    // way to a byte: `xs[i][j]`, not `xs[i]`.
                    if (cur.kind == "str") {
                        error("reading " + type_str(cur) + " out of " + instr.name +
                              " would need a str value, which the language does not "
                              "have -- index to a byte, e.g. " + instr.name +
                              "[i][j] (6.2)");
                        return;
                    }
                    reg_types_[instr.result] = cur;
                } else {
                    if (cur_is_container) {
                        error("cannot store a " + type_str(cur) + " into " + instr.name +
                              " -- the language has no container value to copy (4.1)");
                        return;
                    }
                    if (cur.kind == "str") {
                        error("cannot store a whole " + type_str(cur) + " into " + instr.name +
                              " -- a str is storage, not a value; assign its bytes one "
                              "at a time, e.g. " + instr.name + "[i][j] = v (6.2)");
                        return;
                    }
                    // 4.1. Leaf stores go through check_value_into_target, the
                    // same path a scalar `x: int[32] = 11` takes, so a literal
                    // narrows into a narrower element when it fits and a runtime
                    // value still may not. Checking `val_t != cur` instead made
                    // the two disagree in a way nothing explained: `x: int[32] =
                    // 11` compiled and `xs[0] = 11` into a list[int[32],4] did
                    // not, which left every packed-stride element width
                    // unreachable from the frontend, because the frontend cannot
                    // attach a width to an int literal.
                    check_value_into_target(instr.args.at(n_idx), cur,
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
                // 4.3. A str key is NAMED in name2 rather than held in a
                // register, because a bounded string has no scalar form. The
                // key run is checked against the declared key type here, and the
                // remaining operands shift down by one (DictStore's value is
                // then args[0], not args[1]).
                if (dt.key_kind == "str") {
                    if (instr.name2.empty()) {
                        error(what + " into " + type_str(dt) +
                              " needs a str key variable, e.g. d[k] with k: str[N] (4.3)");
                        return;
                    }
                    auto kit = scope.find(instr.name2);
                    if (kit == scope.end()) {
                        error(what + " into " + type_str(dt) + " names '" + instr.name2 +
                              "' which is not assigned here (V1_SPEC 0.6.10)");
                        return;
                    }
                    if (kit->second.kind != "str") {
                        error(what + " into " + type_str(dt) + " must be " +
                              type_str(dt.key()) + ", got " + type_str(kit->second));
                        return;
                    }
                    // 6.8. The key run's CAPACITY must not exceed the table's key
                    // capacity, because construction copies a key's bytes into a
                    // `key_cap` wide slot and a longer run's content could not
                    // fit -- it would be truncated at construction and then fail
                    // to compare equal at a lookup, with the same text. This is
                    // the static form of the rule a literal already obeys ("a
                    // literal of L bytes fits str[N] only when L < N"), applied
                    // to a str variable as well.
                    if (kit->second.width > dt.key_width) {
                        error(what + " into " + type_str(dt) + ": '" + instr.name2 +
                              "' is " + type_str(kit->second) + ", whose capacity exceeds the "
                              "dict's key capacity of " + std::to_string(dt.key_width) +
                              " (4.3)");
                        return;
                    }
                    if (instr.op == Op::DictStore) {
                        // 6.9. A container value is NAMED (name3) rather than
                        // loaded, because a container is a run of bytes with no
                        // scalar form. Check the named run against the declared
                        // value type the same way a declaration is checked, so a
                        // run that does not match is a type error rather than a
                        // copy of the wrong shape into the value slot.
                        if (!instr.name3.empty()) {
                            check_value_into_target_named(instr.name3, dt.element(), scope,
                                                          "store into " + type_str(dt));
                            return;
                        }
                        if (instr.args.empty()) {
                            error("DictStore needs a value");
                            return;
                        }
                        check_value_into_target(instr.args.at(0), dt.element(),
                                                "store into " + type_str(dt));
                        return;
                    }
                    if (instr.op == Op::DictContains) {
                        reg_types_[instr.result] = LType{"bool", -1};
                    } else if (!instr.name3.empty()) {
                        // 6.9. A container value has no register to land in, so
                        // the frontend gives it a run instead and there is no
                        // result to type. The run itself is what the following
                        // subscripts are checked against.
                    } else {
                        reg_types_[instr.result] = dt.element();
                    }
                    return;
                }
                if (!instr.name2.empty()) {
                    error(what + " into " + type_str(dt) + " carries a str key, but the "
                          "dict is declared with key type " + type_str(dt.key()) + " (4.3)");
                    return;
                }
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
                    if (!instr.name3.empty()) {
                        // 6.9. A container value arrives as a run, so there is no
                        // second operand: `DictStore d, %key, %valrun`.
                        check_value_into_target_named(instr.name3, dt.element(), scope,
                                                      "store into " + type_str(dt));
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
                } else if (!instr.name3.empty()) {
                    // 6.9. A container value copy-out, as in the str-key branch.
                } else {
                    reg_types_[instr.result] = dt.element();
                }
                return;
            }
            // 6.1. A whole-container print. It has no result and no operands;
            // the variable is named and the shape rides the type suffix.
            case Op::PrintContainer:
                check_print_container(instr, scope);
                return;
            // 6.2. `len(s)` on a bounded string. It is a separate op from Len
            // because a string's length is a RUN-TIME property -- the bytes
            // before the NUL -- whereas a container's Len is its compile-time
            // capacity. Reaching either op with the wrong kind of name is a
            // frontend bug, so both are named concretely rather than folded.
            case Op::StrLen: {
                auto it = scope.find(instr.name);
                if (it == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                if (it->second.kind != "str") {
                    error("strlen names " + type_str(it->second) +
                          ", not a str (6.2)");
                    return;
                }
                reg_types_[instr.result] = LType{"int", 64};
                return;
            }
            // 6.2. `s == t` between two bounded strings. Byte-for-byte up to the
            // NUL, so it is a run comparison rather than a scalar `eq`: neither
            // operand is ever a register value. Both names must be str, and they
            // may have different capacities -- comparing str[4] to str[8] is
            // legal and equal when the contents agree.
            case Op::StrEq:
            case Op::StrNe: {
                auto lit = scope.find(instr.name);
                if (lit == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                auto rit = scope.find(instr.name2);
                if (rit == scope.end()) {
                    error("'" + instr.name2 + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                if (lit->second.kind != "str" || rit->second.kind != "str") {
                    error(std::string(instr.op == Op::StrEq ? "streq" : "strne") +
                          " compares two strings, got " +
                          type_str(lit->second) + " and " + type_str(rit->second) + " (6.2)");
                    return;
                }
                reg_types_[instr.result] = LType{"bool", -1};
                return;
            }
            // 6.4. `s.find(sub)` and `s.count(sub)`. Like StrEq these name TWO
            // string runs and produce a scalar; unlike StrEq the result is a
            // full int[64] (an index or a count). Both names must be str, and
            // their capacities may differ.
            case Op::StrFind:
            case Op::StrCount: {
                auto lit = scope.find(instr.name);
                if (lit == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                auto rit = scope.find(instr.name2);
                if (rit == scope.end()) {
                    error("'" + instr.name2 + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                if (lit->second.kind != "str" || rit->second.kind != "str") {
                    error(std::string(instr.op == Op::StrFind ? "strfind" : "strcount") +
                          " scans two strings, got " + type_str(lit->second) + " and " +
                          type_str(rit->second) + " (6.4)");
                    return;
                }
                reg_types_[instr.result] = LType{"int", 64};
                return;
            }
            // 6.7. `s.split(sep)`. Three names: the source str, the separator
            // str, and the DESTINATION list of bounded strings, whose shape
            // rides the trailing suffix. No result -- the piece count is a
            // run-time value and the pieces land in the destination run.
            case Op::StrSplit: {
                auto sit = scope.find(instr.name);
                if (sit == scope.end()) {
                    error("'" + instr.name + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                auto eit = scope.find(instr.name2);
                if (eit == scope.end()) {
                    error("'" + instr.name2 + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                auto dit = scope.find(instr.name3);
                if (dit == scope.end()) {
                    error("'" + instr.name3 + "' is not definitely assigned here (V1_SPEC 0.6.10)");
                    return;
                }
                if (sit->second.kind != "str" || eit->second.kind != "str") {
                    error("strsplit splits a string by a string, got " +
                          type_str(sit->second) + " and " + type_str(eit->second) + " (6.7)");
                    return;
                }
                const LType& dest = dit->second;
                if (dest.kind != "list" || dest.elem_kind != "str") {
                    error("strsplit needs a destination list of bounded strings "
                          "(list[str[K], M]), got " + type_str(dest) + " (6.7)");
                    return;
                }
                // Both tiers lay the run out from the declaration but write it
                // from the suffix, so the suffix must name the destination's own
                // type. Both sides go through from_ir_type, so comparing the two
                // LTops is the shape comparison: a `list[str[8], 4]` suffix and a
                // `list[str[8], 4]` declaration agree; a `list[str[4], 8]` one
                // does not.
                if (!instr.decl_type) {
                    error("strsplit needs a destination type suffix, e.g. "
                          ": list[str[8], 4] (6.7)");
                    return;
                }
                if (!(from_ir_type(*instr.decl_type) == dest)) {
                    error("strsplit destination suffix does not match '" + instr.name3 +
                          "', which is " + type_str(dest) + " (6.7)");
                    return;
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
            case Op::Eq: case Op::Ne: {
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
                    // 4.1 (phase 3). A container is storage, not a value, so
                    // there is nothing to copy: `z = ys` has no defined meaning.
                    // The frontend emits an unannotated store for a container
                    // re-assignment (its declaration already carried the suffix),
                    // so this is exactly where the copy is refused -- naming the
                    // storage rather than letting it degrade into a runtime
                    // "undefined variable" in the interpreter.
                    if (it->second.kind == "list" || it->second.kind == "tuple" ||
                        it->second.is_dict()) {
                        error("cannot assign to '" + instr.name + "' -- " +
                              type_str(it->second) + " is storage, not a value: it is built "
                              "by its declaration and IndexStores, and Lithon has no "
                              "container value to copy (4.1)");
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
                if (!is_compiler_temp(instr.name)) {
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
                if (instr.name == "print" || instr.name == "print_bare") {
                    check_print_call(instr);
                } else if (instr.name == "print_sep" || instr.name == "print_nl") {
                    // 6.1. The separator and final newline of a multi-argument
                    // print are argument-less; they cannot be misused.
                    if (!instr.args.empty()) {
                        error(instr.name + "() takes no arguments");
                    }
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
        LType t = instr.decl_type
                      ? from_ir_type(*instr.decl_type)
                      : LType{instr.type_kind, instr.type_width, instr.type_elem_kind,
                              instr.type_elem_width, instr.type_key_kind, instr.type_key_width};
        // 6.2. A str is a container here: it reserves a run and has a capacity,
        // and the declaration must reach the frame with its name recorded. It is
        // NOT added to the list/tuple branch below, because its element is
        // always the byte int[8] -- there is no declared element to validate,
        // and requiring one would refuse `s: str[16]` for spelling nothing.
        const bool container = t.kind == "list" || t.kind == "tuple" ||
                               t.kind == "ptr" || t.kind == "dict" || t.kind == "str";

        if (t.kind == "str" && t.width <= 0) {
            error("'" + instr.name + ": str requires a capacity, e.g. str[16]");
            return LType{};
        }

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
            if (t.key_kind != "int" && t.key_kind != "bool" && t.key_kind != "str") {
                error("'" + instr.name + ": dict keys are int, bool or str only, got " +
                      type_str(t.key()) + " (4.3)");
                return LType{};
            }
            // 6.9. A dict value may be a container (a list or a tuple) as well as
            // a scalar. A dict value is REFUSED (a dict has three regions, so it
            // has no single leaf run a value slot can hold), exactly as it is as a
            // list element. A str is a scalar here and a length-zero run is
            // checked below, because a str VALUE has its capacity in `cap`, not
            // in `elem_width`.
            if (t.elem_kind != "int" && t.elem_kind != "float" &&
                t.elem_kind != "bool" && t.elem_kind != "str" &&
                t.elem_kind != "list" && t.elem_kind != "tuple") {
                error("'" + instr.name + ": dict values are int, float, bool, str, list or "
                      "tuple, got " + type_str(t.element()) + " (4.3)");
                return LType{};
            }
            if (t.key_kind == "int" &&
                (t.key_width < 0 || t.key_width > 64 ||
                 (t.key_width % 8) != 0)) {
                error("'" + instr.name + ": dict int key width must be a multiple of 8 "
                      "up to 64, got " + std::to_string(t.key_width) + " (4.3)");
                return LType{};
            }
            // 4.3. A str key's "width" is its byte capacity, and the table
            // reserves that many bytes per bucket for it, so it has to be a real
            // positive capacity rather than falling back to some default.
            if (t.key_kind == "str" && t.key_width <= 0) {
                error("'" + instr.name + ": dict str key needs a capacity, e.g. "
                      "dict[str[16], int[64], 4] (4.3)");
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
        // 4.4, second half. A struct is a legal pointee: its bytes ARE its
        // storage, so a pointer to one names the blob and `addressof(p)` is the
        // base of the run. Its width is the byte size, which is what pointer
        // arithmetic scales by. A container is still refused -- an address to a
        // container would have to carry the container's shape, which a pointer
        // cannot.
        if (t.kind == "ptr" && t.elem_kind != "int" && t.elem_kind != "float" &&
            t.elem_kind != "bool" && t.elem_kind != "struct" && t.elem_kind != "str") {
            error("'" + instr.name + ": ptr pointees are int, float, bool or struct or str, got " +
                  type_str(t.element()) + " (4.4)");
            return LType{};
        }
        if (t.kind == "ptr" && t.elem_kind == "struct" && t.elem_width <= 0) {
            error("'" + instr.name + ": ptr[" + type_str(t.element()) + "] needs a struct "
                  "with a known byte size, since pointer arithmetic scales by it (4.4)");
            return LType{};
        }
        // 4.6. A struct's width is its total byte size, so it must be positive
        // and carry no element/key (its shape lives entirely in the frontend's
        // layout, not in the IR type). Zero or negative would size a frame run
        // to nothing and let every field op report out of range.
        if (t.kind == "struct") {
            if (t.width <= 0) {
                error("'" + instr.name + ": struct size must be positive, got " +
                      std::to_string(t.width));
                return LType{};
            }
            if (!t.elem_kind.empty() || !t.key_kind.empty()) {
                error("'" + instr.name + ": struct carries no element or key type "
                      "-- its layout is decided by the frontend (4.6)");
                return LType{};
            }
        }
        // 6.2. A str's width is its byte capacity N, so it must be positive.
        // A non-positive N would size the frame run to nothing (or negative) and
        // turn every byte index into a range error on a string that has no bytes
        // to hold. The element is implicit and must be the byte int[8].
        if (t.kind == "str") {
            if (t.width <= 0) {
                error("'" + instr.name + ": str capacity must be positive, got " +
                      std::to_string(t.width));
                return LType{};
            }
            if (!(t.elem_kind == "int" && t.elem_width == 8)) {
                error("'" + instr.name + ": str carries an implicit int[8] byte "
                      "element, got " + type_str(t.element()) + " (6.2)");
                return LType{};
            }
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
