#include "text_parser.h"

#include <sstream>
#include <stdexcept>
#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <string>

namespace lithon::ir {

namespace {

std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    size_t end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

// Splits on `delim` but ignores delimiters inside [], () or {}, so a container
// annotation survives being a member of a comma-separated list.
//
// Without the bracket depth this silently truncates any type that contains a
// comma of its own: `function g(xs: list[int[64],4])` split its parameter at the
// annotation's own comma and reported "malformed type annotation: list[int[64]",
// pointing at text the programmer never wrote. Every container type in 4.1-4.4
// has this shape -- list[T,N], tuple[T,N], dict[K,V,N] -- so this was going to
// bite 4.2, 4.3 and 4.4 as well, and a confusing truncated-type error is a bad
// way to discover it. Argument lists keep the same behaviour they always had,
// since no value expression can contain a bare bracket pair at that point.
std::vector<std::string> split(const std::string& s, char delim) {
    std::vector<std::string> out;
    std::string cur;
    int depth = 0;
    for (char ch : s) {
        if (ch == '[' || ch == '(' || ch == '{') ++depth;
        else if (ch == ']' || ch == ')' || ch == '}') --depth;
        if (ch == delim && depth <= 0) {
            out.push_back(trim(cur));
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    out.push_back(trim(cur));
    return out;
}

bool starts_with(const std::string& s, const std::string& prefix) {
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

ValueId parse_value_ref(const std::string& tok) {
    if (tok.empty() || tok[0] != '%') {
        throw std::runtime_error("expected value reference starting with '%', got: " + tok);
    }
    return static_cast<ValueId>(std::stoul(tok.substr(1)));
}

// 4.1. A parsed type: the kind, its own width, and -- for a container -- the
// element type inside it. `list[int[64], 10]` is kind "list", capacity 10, and
// element "int" of width 64.
//
// The element type is a nested SUBSCRIPT, so this cannot be the old one-level
// `t.find(']')` scan: for `list[int[64],10]` that finds the `]` belonging to
// `int[64]` and would read the capacity as "64". Splitting on the bracket that
// matches the OUTERMOST one is the whole fix, and it is done by counting depth
// rather than by searching for a comma, so a comma inside a nested subscript
// cannot be mistaken for the capacity separator.
struct ParsedType {
    std::string kind;
    int width = -1;
    std::string elem_kind;
    int elem_width = -1;

    // 4.3. A dict carries a key type as well as a value type. The value is the
    // pair above, so every existing reader of a dict's elements is already
    // correct and does not need to learn about keys.
    std::string key_kind;
    int key_width = -1;

    // The same declaration as a recursive tree, so a nested element survives
    // with its inner shape. The flat fields above are its one-level projection.
    std::shared_ptr<Type> tree;
};

// Splits on commas that are not inside a subscript. `int[64], bool, 8` has a
// comma inside int[64], and treating that one as a separator would read the
// width as 64.
static std::vector<std::string> split_top_level(const std::string& s) {
    std::vector<std::string> parts;
    size_t depth = 0;
    size_t start = 0;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '[') ++depth;
        else if (s[i] == ']') --depth;
        else if (s[i] == ',' && depth == 0) {
            parts.push_back(trim(s.substr(start, i - start)));
            start = i + 1;
        }
    }
    parts.push_back(trim(s.substr(start)));
    return parts;
}

// Index just past the `]` that closes the `[` at `open`, or npos.
static size_t matching_close(const std::string& t, size_t open) {
    int depth = 0;
    for (size_t i = open; i < t.size(); ++i) {
        if (t[i] == '[') ++depth;
        else if (t[i] == ']') {
            if (--depth == 0) return i;
        }
    }
    return std::string::npos;
}

// The type the OLD flat projection called its "width": a scalar's bit width, a
// struct's byte size, or a container's capacity/bucket count. This is the value
// that used to be stacked into elem_width/key_width, so projecting it keeps
// every pre-nesting reader byte-for-byte correct.
static int flat_width(const Type& t) {
    // 6.2. A str[N]'s "width" projection is its byte capacity N, not a bit
    // width, exactly as a container projects its capacity.
    if (t.kind == "str") return t.cap;
    return t.is_container() ? t.cap : t.width;
}

static std::shared_ptr<Type> parse_type_tree(const std::string& raw) {
    const std::string t = trim(raw);
    const size_t bracket = t.find('[');
    auto ty = std::make_shared<Type>();
    if (bracket == std::string::npos) {
        ty->kind = t;
        return ty;
    }
    ty->kind = trim(t.substr(0, bracket));
    const size_t close = matching_close(t, bracket);
    if (close == std::string::npos)
        throw std::runtime_error("malformed type annotation: " + raw);

    const std::string inner = trim(t.substr(bracket + 1, close - bracket - 1));
    // 6.2. `str[N]` is a bounded string: N bytes with an implicit int[8] byte
    // element, so it is laid out and indexed like a one-level container even
    // though its kind is "str" (string printing and run-time length are what
    // make it distinct). Building cap + elem here means every container-shaped
    // reader already sees "N bytes of int[8]" without a second code path.
    if (ty->kind == "str") {
        ty->cap = inner.empty() ? -1 : std::stoi(inner);
        ty->elem = std::make_shared<Type>();
        ty->elem->kind = "int";
        ty->elem->width = 8;
        return ty;
    }
    if (ty->kind != "list" && ty->kind != "tuple" && ty->kind != "ptr" &&
        ty->kind != "dict") {
        // A scalar: `int[8]` -- width is a bit width; `struct[N]` is a byte size.
        ty->width = std::stoi(inner);
        return ty;
    }

    // 4.3. A dict is the one container with three fields, `K, V, N`.
    if (ty->kind == "dict") {
        const std::vector<std::string> parts = split_top_level(inner);
        if (parts.size() != 3)
            throw std::runtime_error("malformed dict type annotation: " + raw);
        ty->key = parse_type_tree(parts[0]);
        ty->elem = parse_type_tree(parts[1]);
        ty->cap = parts[2].empty() ? -1 : std::stoi(parts[2]);
        return ty;
    }

    // A container: `T, N` or, for ptr, just `T`. Split on the top-level comma.
    size_t comma = std::string::npos;
    int depth = 0;
    for (size_t i = 0; i < inner.size(); ++i) {
        if (inner[i] == '[') ++depth;
        else if (inner[i] == ']') --depth;
        else if (inner[i] == ',' && depth == 0) { comma = i; break; }
    }
    // ptr carries no capacity -- `ptr[T]` is complete as written -- so a missing
    // comma is only an error for the sized containers.
    if (comma == std::string::npos) {
        if (ty->kind == "ptr") {
            ty->elem = parse_type_tree(inner);
            return ty;
        }
        throw std::runtime_error("malformed container type annotation: " + raw);
    }

    ty->elem = parse_type_tree(trim(inner.substr(0, comma)));
    const std::string rest = trim(inner.substr(comma + 1));
    ty->cap = rest.empty() ? -1 : std::stoi(rest);
    return ty;
}

ParsedType parse_type_string(const std::string& raw) {
    ParsedType out;
    out.tree = parse_type_tree(raw);
    const Type& ty = *out.tree;
    out.kind = ty.kind;
    out.width = flat_width(ty);
    if (ty.kind == "dict") {
        if (ty.key) {
            out.key_kind = ty.key->kind;
            out.key_width = flat_width(*ty.key);
        }
        if (ty.elem) {
            out.elem_kind = ty.elem->kind;
            out.elem_width = flat_width(*ty.elem);
        }
    } else if (ty.kind == "ptr" || ty.kind == "list" || ty.kind == "tuple" ||
               ty.kind == "str") {
        if (ty.elem) {
            out.elem_kind = ty.elem->kind;
            out.elem_width = flat_width(*ty.elem);
        }
    }
    return out;
}

struct OpAndArgs {
    std::string op_name;
    std::vector<std::string> raw_args;
};

OpAndArgs split_op_and_args(const std::string& rhs) {
    size_t space_pos = rhs.find(' ');
    OpAndArgs result;
    if (space_pos == std::string::npos) {
        result.op_name = rhs;
        return result;
    }
    result.op_name = rhs.substr(0, space_pos);
    std::string arg_str = trim(rhs.substr(space_pos + 1));
    if (!arg_str.empty()) {
        result.raw_args = split(arg_str, ',');
    }
    return result;
}

// Splits "name" (untyped) or "name:Type[N]" (typed) function-header
// parameter into (name, kind, width). kind is "" if untyped.
struct ParamSpec {
    std::string name;
    std::string kind;
    int width = -1;
};

ParamSpec parse_param(const std::string& raw) {
    std::string p = trim(raw);
    size_t colon = p.find(':');
    if (colon == std::string::npos) {
        return ParamSpec{p, "", -1};
    }
    std::string name = trim(p.substr(0, colon));
    const ParsedType pt = parse_type_string(p.substr(colon + 1));
    return ParamSpec{name, pt.kind, pt.width};
}

// Hardening item 5. Decodes the hex-encoded input() prompt carried in the IR.
// The frontend renders the prompt's raw bytes as hex so that any character --
// comma, space, bracket, newline -- survives the comma-split text format. The
// "-" sentinel (or an empty string) means no prompt.
std::string decode_hex_prompt(const std::string& hex) {
    if (hex == "-" || hex.empty()) return std::string();
    if (hex.size() % 2 != 0)
        throw std::runtime_error("inputint: prompt hex has an odd number of digits");
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    std::string out;
    out.reserve(hex.size() / 2);
    for (size_t i = 0; i < hex.size(); i += 2) {
        const int hi = nibble(hex[i]);
        const int lo = nibble(hex[i + 1]);
        if (hi < 0 || lo < 0)
            throw std::runtime_error("inputint: prompt hex has a non-hex digit");
        out.push_back(static_cast<char>((hi << 4) | lo));
    }
    return out;
}

} // namespace

Module parse_ir_text(const std::string& text) {
    Module module;
    Function* current_fn = nullptr;
    BasicBlock* current_block = nullptr;

    std::istringstream stream(text);
    std::string raw_line;

    while (std::getline(stream, raw_line)) {
        std::string line = trim(raw_line);
        if (line.empty()) continue;

        if (starts_with(line, "function ")) {
            std::string rest = line.substr(std::string("function ").size());
            if (!rest.empty() && rest.back() == ':') rest.pop_back();

            // Optional " -> ReturnType" before the (now-removed) trailing ':'
            std::string return_kind;
            int return_width = -1;
            size_t arrow = rest.find("->");
            if (arrow != std::string::npos) {
                std::string ret_str = trim(rest.substr(arrow + 2));
                const ParsedType rt = parse_type_string(ret_str);
                return_kind = rt.kind;
                return_width = rt.width;
                rest = trim(rest.substr(0, arrow));
            }

            size_t paren_open = rest.find('(');
            size_t paren_close = rest.find(')');
            std::string name = rest.substr(0, paren_open);
            std::vector<std::string> params;
            std::vector<std::string> param_kinds;
            std::vector<int> param_widths;

            if (paren_open != std::string::npos && paren_close != std::string::npos) {
                std::string param_str = trim(rest.substr(paren_open + 1, paren_close - paren_open - 1));
                if (!param_str.empty()) {
                    for (const auto& raw_param : split(param_str, ',')) {
                        ParamSpec spec = parse_param(raw_param);
                        params.push_back(spec.name);
                        param_kinds.push_back(spec.kind);
                        param_widths.push_back(spec.width);
                    }
                }
            }

            Function fn;
            fn.name = name;
            fn.params = params;
            fn.param_type_kinds = param_kinds;
            fn.param_type_widths = param_widths;
            fn.return_type_kind = return_kind;
            fn.return_type_width = return_width;
            module.functions.push_back(fn);
            current_fn = &module.functions.back();
            current_block = nullptr;
            continue;
        }

        if (line.back() == ':' && line.find('=') == std::string::npos) {
            if (!current_fn) {
                throw std::runtime_error("block label outside of any function: " + line);
            }
            std::string label = line.substr(0, line.size() - 1);
            current_fn->blocks.push_back(BasicBlock{label, {}});
            current_block = &current_fn->blocks.back();
            continue;
        }

        if (!current_block) {
            throw std::runtime_error("instruction outside of any block: " + line);
        }

        Instr instr;
        instr.result = kInvalidValue;

        size_t eq_pos = line.find('=');
        std::string rhs;
        if (eq_pos != std::string::npos) {
            std::string lhs = trim(line.substr(0, eq_pos));
            instr.result = parse_value_ref(lhs);
            rhs = trim(line.substr(eq_pos + 1));
        } else {
            rhs = line;
        }

        // Optional trailing " : Type[N]" type suffix (currently only
        // emitted on typed `store` instructions).
        size_t type_sep = rhs.find(" : ");
        if (type_sep != std::string::npos) {
            std::string type_str = trim(rhs.substr(type_sep + 3));
            const ParsedType pt = parse_type_string(type_str);
            instr.type_kind = pt.kind;
            instr.type_width = pt.width;
            instr.type_elem_kind = pt.elem_kind;
            instr.type_elem_width = pt.elem_width;
            instr.type_key_kind = pt.key_kind;
            instr.type_key_width = pt.key_width;
            instr.decl_type = pt.tree;
            rhs = trim(rhs.substr(0, type_sep));
        }

        OpAndArgs oa = split_op_and_args(rhs);

        if (oa.op_name == "const_i64") {
            instr.op = Op::ConstInt;
            instr.int_imm = std::stoll(oa.raw_args.at(0));
        } else if (oa.op_name == "const_f64") {
            instr.op = Op::ConstFloat;
            // strtod directly, not std::stod. stod throws std::out_of_range
            // whenever strtod reports ERANGE, and glibc sets ERANGE for a
            // *subnormal* result, not just an overflowing one -- so every
            // literal below about 2.2e-308 (1e-309, 5e-324, the smallest
            // subnormal) was rejected as "error: stod" even though the value
            // is perfectly representable. CPython accepts all of them.
            // Underflow to a subnormal is the correct IEEE result, so ERANGE
            // is deliberately not treated as an error; only a genuinely
            // unparseable token is rejected.
            const std::string& lit = oa.raw_args.at(0);
            char* end = nullptr;
            instr.float_imm = std::strtod(lit.c_str(), &end);
            if (end == lit.c_str() || *end != '\0') {
                throw std::runtime_error("const_f64 is not a number: " + lit);
            }
        } else if (oa.op_name == "const_bool") {
            instr.op = Op::ConstBool;
            instr.int_imm = std::stoll(oa.raw_args.at(0));
        } else if (oa.op_name == "load") {
            instr.op = Op::Load;
            instr.name = oa.raw_args.at(0);
        } else if (oa.op_name == "store") {
            instr.op = Op::Store;
            instr.name = oa.raw_args.at(0);
            // 4.1. A container declaration carries no value: `store xs :
            // list[int[64],4]` RESERVES the run of slots and binds the name. It
            // is not an assignment, so there is nothing to store and requiring
            // a value operand here is what made every container declaration
            // impossible to express.
            if (oa.raw_args.size() >= 2) instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        // 4.1. Container ops. Their first argument is a container VARIABLE name,
        // not a ValueId: the address is derived from the variable's slot run,
        // so it is carried in `name` and only the real operands are refs.
        } else if (oa.op_name == "Index") {
            // A nested access is one instruction with a whole chain of indices
            // (see ContainerLayout): `Index xs, i, j` has name "xs" and indices
            // i, j. A one-level `Index xs, i` is the chain of length one, so
            // every existing producer and reader is unchanged.
            instr.op = Op::Index;
            instr.name = oa.raw_args.at(0);
            for (size_t a = 1; a < oa.raw_args.size(); ++a)
                instr.args.push_back(parse_value_ref(oa.raw_args.at(a)));
        } else if (oa.op_name == "IndexStore") {
            // Same chain, but the LAST argument is the value being written and
            // every argument before it is an index: `IndexStore xs, i, j, %v`.
            instr.op = Op::IndexStore;
            instr.name = oa.raw_args.at(0);
            for (size_t a = 1; a + 1 < oa.raw_args.size(); ++a)
                instr.args.push_back(parse_value_ref(oa.raw_args.at(a)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(oa.raw_args.size() - 1)));
        } else if (oa.op_name == "Len") {
            instr.op = Op::Len;
            instr.name = oa.raw_args.at(0);
        // 6.2. String ops. Same `name`-carries-the-variable convention; StrEq
        // names a SECOND variable as well, which lands in name2.
        } else if (oa.op_name == "strlen") {
            instr.op = Op::StrLen;
            instr.name = oa.raw_args.at(0);
        } else if (oa.op_name == "streq") {
            instr.op = Op::StrEq;
            instr.name = oa.raw_args.at(0);
            instr.name2 = oa.raw_args.at(1);
        // 6.4. The two substring scans. Same two-name convention as StrEq: the
        // haystack is `name`, the needle is `name2`.
        } else if (oa.op_name == "strfind") {
            instr.op = Op::StrFind;
            instr.name = oa.raw_args.at(0);
            instr.name2 = oa.raw_args.at(1);
        } else if (oa.op_name == "strcount") {
            instr.op = Op::StrCount;
            instr.name = oa.raw_args.at(0);
            instr.name2 = oa.raw_args.at(1);
        // 6.7. Three names: the source str, the separator str, and the
        // destination list of bounded strings. The destination's shape rides the
        // trailing type suffix.
        } else if (oa.op_name == "strsplit") {
            instr.op = Op::StrSplit;
            instr.name = oa.raw_args.at(0);
            instr.name2 = oa.raw_args.at(1);
            instr.name3 = oa.raw_args.at(2);
        // 4.3. Dict ops. Same convention as the container ops above: the first
        // argument is the dict VARIABLE, so it lands in `name` and only the key
        // and the value are real operand references.
        //
        // A key that is a str is a NAME, not a value reference: a bounded string
        // has no scalar form to put in a register, so the run is named in `name2`
        // and the value shifts down into args[0]. The two forms are told apart by
        // the token itself -- a register always starts with '%', a run never
        // does -- so the parser does not need the dict's declared key type.
        } else if (oa.op_name == "DictStore") {
            instr.op = Op::DictStore;
            instr.name = oa.raw_args.at(0);
            // 6.9. Up to two of the three operands may be NAMES rather than
            // registers: a str key (name2) and a container value (name3). They
            // are independent -- `dict[str[N], list[T, M], B]` has both -- so
            // each is classified on its own. A container value is the LAST
            // operand, so it is peeled first and the rest read as (key, value)
            // or (key); then the key slot is a register or a named run.
            size_t nargs = oa.raw_args.size();
            if (nargs >= 3 && !oa.raw_args.back().empty() && oa.raw_args.back()[0] != '%') {
                instr.name3 = oa.raw_args.back();
                --nargs;
            }
            if (!oa.raw_args.at(1).empty() && oa.raw_args.at(1)[0] == '%') {
                // A register key. The value, if the operand list still holds
                // one, is the next slot; a peeled container value leaves none.
                instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
                if (nargs >= 3) instr.args.push_back(parse_value_ref(oa.raw_args.at(2)));
            } else {
                instr.name2 = oa.raw_args.at(1);
                if (nargs >= 3) instr.args.push_back(parse_value_ref(oa.raw_args.at(2)));
            }
        } else if (oa.op_name == "DictIndex") {
            instr.op = Op::DictIndex;
            instr.name = oa.raw_args.at(0);
            // 6.9. A container value is copied OUT into a named run rather than
            // into a register, so the read names it as a third argument (the same
            // split DictStore uses for a named value, at the other end).
            if (oa.raw_args.size() >= 3 && !oa.raw_args.back().empty() &&
                oa.raw_args.back()[0] != '%') {
                instr.name3 = oa.raw_args.back();
            }
            if (!oa.raw_args.at(1).empty() && oa.raw_args.at(1)[0] == '%')
                instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
            else
                instr.name2 = oa.raw_args.at(1);
        } else if (oa.op_name == "DictContains") {
            instr.op = Op::DictContains;
            instr.name = oa.raw_args.at(0);
            if (!oa.raw_args.at(1).empty() && oa.raw_args.at(1)[0] == '%')
                instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
            else
                instr.name2 = oa.raw_args.at(1);
        // 6.1. Printing a whole container. Same convention as the container ops
        // above: the first argument is the container VARIABLE, so it lands in
        // `name`; the shape rides the generic trailing " : T" suffix already
        // parsed above into the type fields. No operands, no result.
        } else if (oa.op_name == "print_container") {
            instr.op = Op::PrintContainer;
            instr.name = oa.raw_args.at(0);
            // Phase 6.7. A list names its live-length companion as a SECOND
            // argument (`print_container xs, xs.__len : ...`); a tuple, dict or
            // str has none and names only the run.
            if (oa.raw_args.size() >= 2) instr.name2 = oa.raw_args.at(1);
        // 4.4. Pointer ops. AddressOf names the target VARIABLE like the
        // container ops; ValueOf's pointer arrives as a real operand ValueId.
        // `%v = valueof %p : int[64]` already works: the generic trailing
        // " : T" suffix is parsed before we get here and lands in the element
        // pair, which is where ValueOf's pointee is expected.
        } else if (oa.op_name == "addressof") {
            instr.op = Op::AddressOf;
            instr.name = oa.raw_args.at(0);
        } else if (oa.op_name == "valueof") {
            instr.op = Op::ValueOf;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
        // 4.6. Struct field ops. Same `name`-carries-the-variable convention as
        // the container ops; the byte offset is a compile-time layout constant
        // and is read straight into int_imm, and the field type arrives in the
        // trailing " : T" suffix (parsed above). `%r = fieldload s, 4 : int[16]`
        // and `fieldstore s, 4, %v`.
        } else if (oa.op_name == "fieldload") {
            instr.op = Op::FieldLoad;
            instr.int_imm = std::stoll(oa.raw_args.at(1));
            // 4.4, second half. The subject is either a struct VARIABLE name
            // (the long-standing form) or, when it starts with '%', a pointer
            // BASE register -- so a field can be read through `_p` and reach
            // the same bytes the named form does. A base leaves `name` empty,
            // which is what tells the checker and both engines to dereference.
            if (!oa.raw_args.at(0).empty() && oa.raw_args.at(0)[0] == '%') {
                instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            } else {
                instr.name = oa.raw_args.at(0);
            }
        } else if (oa.op_name == "fieldstore") {
            instr.op = Op::FieldStore;
            instr.int_imm = std::stoll(oa.raw_args.at(1));
            // A store's operands are VALUE first, then the optional base, so
            // the value and the pointer can never collide.
            instr.args.push_back(parse_value_ref(oa.raw_args.at(2)));
            if (!oa.raw_args.at(0).empty() && oa.raw_args.at(0)[0] == '%') {
                instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            } else {
                instr.name = oa.raw_args.at(0);
            }
        } else if (oa.op_name == "tofloat") {
            instr.op = Op::ToFloat;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
        } else if (oa.op_name == "toint") {
            instr.op = Op::ToInt;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
        } else if (oa.op_name == "inputint") {
            instr.op = Op::InputInt;
            instr.name = oa.raw_args.empty() ? std::string()
                                             : decode_hex_prompt(oa.raw_args.at(0));
        } else if (oa.op_name == "guardnonzero") {
            instr.op = Op::GuardNonZero;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
        // Phase III. The raw syscall: every operand (the number first, then
        // up to six arguments) is a real ValueId, parsed generically. Missing
        // trailing arguments are simply absent from the vector.
        } else if (oa.op_name == "syscall") {
            instr.op = Op::Syscall;
            for (const auto& raw : oa.raw_args) {
                instr.args.push_back(parse_value_ref(raw));
            }
        } else if (oa.op_name == "add") {
            instr.op = Op::Add;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "sub") {
            instr.op = Op::Sub;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "mul") {
            instr.op = Op::Mul;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "wrapadd") {
            instr.op = Op::WrapAdd;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "wrapsub") {
            instr.op = Op::WrapSub;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "wrapmul") {
            instr.op = Op::WrapMul;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "div") {
            instr.op = Op::Div;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "mod") {
            instr.op = Op::Mod;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "lt") {
            instr.op = Op::Lt;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "gt") {
            instr.op = Op::Gt;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "eq") {
            instr.op = Op::Eq;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "and") {
            instr.op = Op::And;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "or") {
            instr.op = Op::Or;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "not") {
            instr.op = Op::Not;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
        } else if (oa.op_name == "shl" || oa.op_name == "shr"
                   || oa.op_name == "band" || oa.op_name == "bor"
                   || oa.op_name == "bxor") {
            // Bitwise/shift, distinct from the logical and/or above.
            if (oa.op_name == "shl") instr.op = Op::Shl;
            else if (oa.op_name == "shr") instr.op = Op::Shr;
            else if (oa.op_name == "band") instr.op = Op::BitAnd;
            else if (oa.op_name == "bor") instr.op = Op::BitOr;
            else instr.op = Op::BitXor;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "branch") {
            instr.op = Op::Branch;
            instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            instr.name = oa.raw_args.at(1) + "," + oa.raw_args.at(2);
        } else if (oa.op_name == "jump") {
            instr.op = Op::Jump;
            instr.name = oa.raw_args.at(0);
        } else if (oa.op_name == "call") {
            instr.op = Op::Call;
            instr.name = oa.raw_args.at(0);
            for (size_t i = 1; i < oa.raw_args.size(); ++i) {
                instr.args.push_back(parse_value_ref(oa.raw_args[i]));
            }
        } else if (oa.op_name == "return") {
            instr.op = Op::Return;
            if (!oa.raw_args.empty()) {
                instr.args.push_back(parse_value_ref(oa.raw_args.at(0)));
            }
        } else {
            throw std::runtime_error("unrecognized IR opcode: " + oa.op_name);
        }

        current_block->instrs.push_back(instr);
    }

    return module;
}

} // namespace lithon::ir
