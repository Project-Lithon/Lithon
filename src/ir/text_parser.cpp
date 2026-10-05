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
};

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

ParsedType parse_type_string(const std::string& raw) {
    std::string t = trim(raw);
    size_t bracket = t.find('[');
    if (bracket == std::string::npos) return ParsedType{t, -1, "", -1};

    ParsedType out;
    out.kind = trim(t.substr(0, bracket));
    const size_t close = matching_close(t, bracket);
    if (close == std::string::npos)
        throw std::runtime_error("malformed type annotation: " + raw);

    const std::string inner = trim(t.substr(bracket + 1, close - bracket - 1));
    if (out.kind != "list" && out.kind != "tuple" && out.kind != "ptr") {
        // A scalar: `int[8]` -- width is a bit width.
        out.width = std::stoi(inner);
        return out;
    }

    // A container: `int[64], 10` or, for ptr, just `int[64]`. Split on the
    // top-level comma only.
    size_t comma = std::string::npos;
    int depth = 0;
    for (size_t i = 0; i < inner.size(); ++i) {
        if (inner[i] == '[') ++depth;
        else if (inner[i] == ']') --depth;
        else if (inner[i] == ',' && depth == 0) { comma = i; break; }
    }
    // ptr carries no capacity -- `ptr[int[64]]` is complete as written -- so a
    // missing comma is only an error for the sized containers.
    if (comma == std::string::npos) {
        if (out.kind == "ptr") {
            const ParsedType e = parse_type_string(trim(inner));
            out.elem_kind = e.kind;
            out.elem_width = e.width;
            return out;
        }
        throw std::runtime_error("malformed container type annotation: " + raw);
    }

    const std::string elem = trim(inner.substr(0, comma));
    const std::string rest = trim(inner.substr(comma + 1));

    // The element is itself a type string, so `list[list[int[8],4],2]` nests.
    const ParsedType e = parse_type_string(elem);
    out.elem_kind = e.kind;
    out.elem_width = e.width;
    if (e.width < 0 && e.kind == "list") {
        // A list element with no width carries its capacity in the element slot;
        // keep it addressable rather than silently flattening to -1.
        out.elem_width = e.width;
    }
    out.width = rest.empty() ? -1 : std::stoi(rest);
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
            instr.op = Op::Index;
            instr.name = oa.raw_args.at(0);
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
        } else if (oa.op_name == "IndexStore") {
            instr.op = Op::IndexStore;
            instr.name = oa.raw_args.at(0);
            instr.args.push_back(parse_value_ref(oa.raw_args.at(1)));
            instr.args.push_back(parse_value_ref(oa.raw_args.at(2)));
        } else if (oa.op_name == "Len") {
            instr.op = Op::Len;
            instr.name = oa.raw_args.at(0);
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
