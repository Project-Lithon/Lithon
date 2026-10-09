// Pins the typechecker's rules for putting a value into a typed location,
// which is where the two typecheckers (this one and tools/typecheck.py) are
// easiest to let drift apart.
//
// The case that motivated all of this: `j: float[64] = 0` used to be accepted
// here, because the literal branch only range-checked int targets and then
// returned. The frontend stored an int into a float[64] variable, the type
// checker said "OK", and the program only failed much later in codegen, with
// the print guard complaining that "a value stored both an int and a float
// reaches here" -- a message that pointed at the loop and never mentioned the
// literal the user actually wrote. An int literal is not a float, and a
// float-typed variable holds a float, so this is now a type error naming the
// literal and suggesting the fix.
//
// Two rules that must NOT be confused with it, both covered below:
// expression-level promotion (`7 + 0.5` is 7.5), which is still allowed, and
// int -> float on ASSIGNMENT for a non-literal (`b: float[64] = a` where a is
// an int). That second one used to promote; V1_SPEC 0.6.11 now says Lithon does
// not convert on assignment, so it is rejected exactly like the literal case.

#include "typecheck.h"
#include "ir/text_parser.h"
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

using lithon::ir::parse_ir_text;
using lithon::typecheck::check_module;

namespace {

int failures = 0;

std::string errors_for(const std::string& ir) {
    // A malformed type annotation is a hard error in the IR text parser rather
    // than a diagnostic from the checker, so a case that reaches it would
    // otherwise take the whole test binary down instead of reporting one
    // failure. It is still a rejection, and these tests are about which layer
    // refuses an illegal program as much as about the message.
    lithon::ir::Module module;
    try {
        module = parse_ir_text(ir);
    } catch (const std::exception& e) {
        return std::string("parser refused the IR: ") + e.what();
    }
    std::string joined;
    for (const auto& e : check_module(module)) {
        if (!joined.empty()) joined += " | ";
        joined += e.message;
    }
    return joined;
}

void expect_accepts(const char* what, const std::string& ir) {
    std::string errs = errors_for(ir);
    if (errs.empty()) {
        std::printf("  ok   %s\n", what);
    } else {
        std::printf("  FAIL %s: expected accepted, got: %s\n", what, errs.c_str());
        ++failures;
    }
}

void expect_rejects(const char* what, const std::string& ir, const char* needle) {
    std::string errs = errors_for(ir);
    if (errs.find(needle) == std::string::npos) {
        std::printf("  FAIL %s: expected a rejection mentioning \"%s\", got: %s\n",
                    what, needle, errs.empty() ? "(accepted)" : errs.c_str());
        ++failures;
    } else {
        std::printf("  ok   %s\n", what);
    }
}

const char* kFloatDeclIntLiteral = R"(
function __main__():
block0:
    %0 = const_i64 0
    store j, %0 : float[64]
    %1 = load j
    call print, %1
    return
)";

} // namespace

int main() {
    std::printf("typecheck: literal kind vs declared type\n");

    // The regression this file exists for.
    expect_rejects("int literal into float[64] declaration", kFloatDeclIntLiteral,
                   "is an int, but float[64] must hold a float -- write 0.0");

    // Same rule on re-assignment, where the value is no longer the initialiser.
    expect_rejects("int literal re-assigned to a float variable", R"(
function __main__():
block0:
    %0 = const_f64 0.0
    store j, %0 : float[64]
    %1 = const_i64 5
    store j, %1
    return
)", "is an int, but float[64] must hold a float -- write 5.0");

    // The correct spelling, and the reason the message can suggest it.
    expect_accepts("float literal into float[64]", R"(
function __main__():
block0:
    %0 = const_f64 0.0
    store j, %0 : float[64]
    return
)");

    // No conversion on assignment (V1_SPEC 0.6.11): an int VARIABLE is not a
    // float either. This used to be accepted (tests/typed_programs/
    // int_to_float_ok.py); the checker moved and this expectation had not.
    // tools/typecheck.py and that .py program must agree with this rule.
    expect_rejects("int VARIABLE into float[64] is rejected", R"(
function __main__():
block0:
    %0 = const_i64 5
    store a, %0 : int[64]
    %1 = load a
    store b, %1 : float[64]
    return
)", "a float-typed location must hold a float");

    // Expression-level promotion is a different rule and never comes through
    // the assignment path: 7 + 0.5 is 7.5, not a rejection.
    expect_accepts("expression promotion 7 + 0.5", R"(
function __main__():
block0:
    %0 = const_i64 7
    %1 = const_f64 0.5
    %2 = add %0, %1
    call print, %2
    return
)");

    // int % int stays int, so an int literal is correct there.
    expect_accepts("int literal into int[64]", R"(
function __main__():
block0:
    %0 = const_i64 0
    store i, %0 : int[64]
    return
)");

    // The pre-existing literal-range rule must be untouched by any of this.
    expect_rejects("int literal that does not fit int[8]", R"(
function __main__():
block0:
    %0 = const_i64 199
    store x, %0 : int[8]
    return
)", "does not fit int[8]");

    // And float -> int remains a hard error with its own message.
    expect_rejects("float into int[8]", R"(
function __main__():
block0:
    %0 = const_f64 3.5
    store x, %0 : int[8]
    return
)", "float -> int conversion does not exist");

    // --- bitwise and shifts -------------------------------------------------
    // These pin the rules that the two checkers must agree on, in both
    // directions: the rejections below have to be rejections in
    // tools/typecheck.py too, and the acceptances have to be acceptances.

    // A left shift multiplies, so a value that fits its own declared type can
    // still leave the type it is being stored into. 100 << 3 is 800, which is
    // not an int[8].
    expect_rejects("shl overflowing the target int[8]", R"(
function __main__():
block0:
    %0 = const_i64 100
    store a, %0 : int[8]
    %1 = load a
    %2 = const_i64 3
    %3 = shl %1, %2
    store b, %3 : int[8]
    return
)", "is not wide enough");

    // The same shift into a target that can hold it is fine. This is the
    // negative case that keeps the rule from being "reject every shl".
    expect_accepts("shl that fits the target int[16]", R"(
function __main__():
block0:
    %0 = const_i64 100
    store a, %0 : int[8]
    %1 = load a
    %2 = const_i64 3
    %3 = shl %1, %2
    store b, %3 : int[16]
    return
)");

    // ---------------------------------------------------------------- 4.1
    // Containers. Two rules are pinned here.
    //
    // First: a scalar literal cannot BUILD a container. Before 4.1 the literal
    // branch of check_value_into_target only knew int and float targets, so
    // `store ys, %int : list[float[64],4]` fell through and was accepted --
    // a typed variable whose declaration constrained nothing. That made the
    // element-type comparison below untestable: every container case fed it an
    // int, and was being rejected for the wrong reason.
    expect_rejects("int literal cannot build list[int[64],4]", R"(
function __main__():
block0:
    %0 = const_i64 1
    store xs, %0 : list[int[64],4]
    return
)", "a literal cannot build");

    expect_rejects("int literal cannot build ptr[int[64]]", R"(
function __main__():
block0:
    %0 = const_i64 1
    store _p, %0 : ptr[int[64]]
    return
)", "a literal cannot build");

    // Second: shape validation happens before anything else, so a malformed
    // container names its own problem instead of the conversion.
    expect_rejects("capacity 0 rejected", R"(
function __main__():
block0:
    %0 = const_i64 1
    store xs, %0 : list[int[64],0]
    return
)", "capacity must be positive");

    expect_rejects("negative capacity rejected", R"(
function __main__():
block0:
    %0 = const_i64 1
    store xs, %0 : list[int[64],-3]
    return
)", "capacity must be positive");

    expect_rejects("list with no element type rejected", R"(
function __main__():
block0:
    %0 = const_i64 1
    store xs, %0 : list[,4]
    return
)", "needs an element type");

    // ptr renders without a phantom capacity, and a nested element renders as
    // `list[...]` rather than as its bare capacity -- `list[list[4], 2]` reads
    // like a list of 4-element ints, which is not what it is.
    expect_rejects("ptr renders with no capacity", R"(
function __main__():
block0:
    %0 = const_i64 1
    store _p, %0 : ptr[int[64]]
    return
)", "ptr[int[64]]");

    expect_rejects("nested list renders its element as list[...]", R"(
function __main__():
block0:
    %0 = const_i64 1
    store g, %0 : list[list[int[8],4],2]
    return
)", "list[list[...], 2]");

    // 4.1 container access. The declarations below use a container that was
    // already built, so the shape rules are tested on their own terms: an
    // IndexStore into it is what a real program does, and it is the only way to
    // get a list-typed VALUE to feed the element-type check.
    expect_accepts("Index and IndexStore into list[int[64],4]", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %1 = const_i64 0
    IndexStore xs, %1, %0
    %2 = Index xs, %1
    store n, %2 : int[64]
    return
)");

    // Static-only capacity overflow: the index is a literal and N is part of the
    // type, so this is decidable at compile time rather than being left to a
    // runtime check.
    expect_rejects("literal index == capacity", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %1 = const_i64 4
    %2 = Index xs, %1
    return
)", "out of range for list[int[64], 4]");

    expect_rejects("negative literal index", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %1 = const_i64 -1
    %2 = Index xs, %1
    return
)", "out of range for list[int[64], 4]");

    // Indexing a scalar must be a type error, not an address computed from a
    // register. This is the rule that keeps Index off the scalar path entirely.
    expect_rejects("indexing a scalar int[64]", R"(
function __main__():
block0:
    %0 = const_i64 1
    store s, %0 : int[64]
    %1 = const_i64 0
    %2 = Index s, %1
    return
)", "not a container");

    expect_rejects("len() of a scalar", R"(
function __main__():
block0:
    %0 = const_i64 1
    store s, %0 : int[64]
    %1 = Len s
    return
)", "not a container");

    // THE element-type check. It has to differ from the container in a way the
    // container's OWN (kind, width) cannot see, or the test passes for the wrong
    // reason: a float into list[int[64],4] is already caught by the KINDS
    // differing, so dropping the element comparison from LType::operator== still
    // rejects it. list[int[8],4] against an int[64] value is same-kind,
    // same-capacity, different-element-width -- only the recursive part of the
    // type can refuse it. (Verified by mutation.)
    //
    // The value is a PARAMETER, not a literal. An int literal narrows into a
    // narrower element whenever it fits -- the rule a scalar `x: int[8] = 2`
    // already follows, and the one that makes list[int[32],N] reachable from
    // the frontend, which cannot put a width on a literal. A runtime int[64]
    // still may not, so a parameter is what keeps this test pointed at the
    // element-type comparison itself.
    expect_rejects("int[64] stored into a list[int[8],4]", R"(
function __main__():
block0:
    %0 = const_i64 5
    store v, %0 : int[64]
    %1 = load v
    store xs : list[int[8],4]
    %2 = const_i64 0
    IndexStore xs, %2, %1
    return
)", "cannot narrow int[64] into int[8]");

    // The companion to the case above: the same int[8] element accepts a
    // literal that fits. Both halves matter -- accepting every literal would
    // hide an out-of-range one, and rejecting every literal would leave the
    // packed-stride widths unusable from Python.
    expect_accepts("fitting literal stored into a list[int[8],4]", R"(
function __main__():
block0:
    store xs : list[int[8],4]
    %1 = const_i64 2
    %2 = const_i64 0
    IndexStore xs, %2, %1
    return
)");

    expect_rejects("out-of-range literal stored into a list[int[8],4]", R"(
function __main__():
block0:
    store xs : list[int[8],4]
    %1 = const_i64 300
    %2 = const_i64 0
    IndexStore xs, %2, %1
    return
)", "does not fit int[8]");

    expect_accepts("float stored into a list[float[64],4]", R"(
function __main__():
block0:
    store xs : list[float[64],4]
    %1 = const_f64 1.5
    %2 = const_i64 0
    IndexStore xs, %2, %1
    return
)");

    // Len is typed as a full-width int even though codegen folds it.
    expect_accepts("Len result is usable as an int", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %1 = Len xs
    store n, %1 : int[64]
    return
)");

    // The index guard in compile_function.h only fires for an index that is NOT
    // a compile-time constant, so the typechecker must keep accepting that
    // shape: if it started rejecting a computed index, the runtime guard would
    // become unreachable and its test coverage would silently vanish.
    expect_accepts("dynamic index into list[int[64],4]", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %0 = const_i64 3
    store i, %0 : int[64]
    %1 = load i
    %2 = Index xs, %1
    store n, %2 : int[64]
    return
)");

    // Boundary of the static rule: capacity-1 is the last legal literal index.
    // Together with the "literal index == capacity" rejection above, this pins
    // the off-by-one from both sides.
    expect_accepts("literal index capacity-1", R"(
function __main__():
block0:
    store xs : list[int[64],4]
    %1 = const_i64 3
    %2 = Index xs, %1
    store n, %2 : int[64]
    return
)");

    // A float element read out of a list is a float: it must satisfy a
    // float[64] declaration. This is the typing half of the float-element-load
    // path (the encoder half -- REX before the F2 prefix -- is tracked
    // separately and is not pinned here).
    expect_accepts("float element load into float[64]", R"(
function __main__():
block0:
    store xs : list[float[64],4]
    %1 = const_i64 0
    %2 = Index xs, %1
    store d, %2 : float[64]
    return
)");

    // A count outside 0..63 is a compile-time error when it is a literal,
    // because x86 masks the count to 6 bits and would otherwise silently
    // shift by a different amount (64 would execute as 0, -1 as 63).
    expect_rejects("literal shift count 64", R"(
function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 64
    %2 = shl %0, %1
    return
)", "out of range 0..63");

    expect_rejects("negative literal shift count", R"(
function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 -1
    %2 = shl %0, %1
    return
)", "out of range 0..63");

    // 63 is the largest legal count and must be accepted, not lumped in with
    // the rejections above.
    expect_accepts("literal shift count 63", R"(
function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 63
    %2 = shl %0, %1
    return
)");

    // Bitwise ops are integer-only. There is no float bit pattern to
    // reinterpret, so this is a type error rather than a punning of the
    // double's bytes.
    expect_rejects("float operand to a bitwise op", R"(
function __main__():
block0:
    %0 = const_f64 2.5
    %1 = const_i64 1
    %2 = band %0, %1
    return
)", "integer-only");

    expect_accepts("integer bitwise and", R"(
function __main__():
block0:
    %0 = const_i64 5
    %1 = const_i64 3
    %2 = band %0, %1
    return
)");

    // A binop's result is the LEFT operand's width, so bitand of int[8] and
    // int[64] is an int[8] and may be stored in an int[8] without narrowing.
    expect_accepts("bitand result takes the left width", R"(
function __main__():
block0:
    %0 = const_i64 5
    store a, %0 : int[8]
    %1 = const_i64 3
    %2 = load a
    %3 = band %2, %1
    store b, %3 : int[8]
    return
)");

    // check_binop_fits_target answers whether the VALUES fit the target, which
    // is a different question from whether the result's WIDTH fits. An int[64]
    // result holding a small number is still an illegal narrowing, and the
    // early return that used to skip this check let `return 100 << 3` through
    // as int[16] while tools/typecheck.py rejected it.
    expect_rejects("shl result narrowed into a smaller int", R"(
function f() -> int[16]:
block0:
    %0 = const_i64 100
    %1 = const_i64 3
    %2 = shl %0, %1
    return %2
    return
)", "cannot narrow");

    // A conditional expression lowers to a temporary named __ifexprN that
    // both arms store and the join loads. The name is minted by the frontend,
    // so there is no source annotation for V1_SPEC 0.6.1 to demand -- the type
    // has to be inferred from the arms, or every typed program using a ternary
    // is rejected for a declaration it cannot have.
    expect_accepts("merge temp inferred from int arms", R"(
function __main__():
block0:
    %0 = const_i64 1
    %1 = const_i64 0
    branch %0, block1, block2
block1:
    %2 = const_i64 7
    store __ifexpr0, %2
    jump block3
block2:
    %3 = const_i64 9
    store __ifexpr0, %3
    jump block3
block3:
    %4 = load __ifexpr0
    call print, %4
    return
)");

    // The inference must produce the ARM's type, not merely "some type": a
    // merge temp that came back as an int would put an int where the join
    // expects the float, and print the wrong thing.
    expect_accepts("merge temp inferred as float from float arms", R"(
function __main__():
block0:
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_f64 1.5
    store __ifexpr0, %1
    jump block3
block2:
    %2 = const_f64 2.5
    store __ifexpr0, %2
    jump block3
block3:
    %3 = load __ifexpr0
    call print, %3
    return
)");

    // ...and it is the float type in fact, not a pass: the merged value has to
    // satisfy a float[64] declaration.
    expect_accepts("merged float satisfies a float[64] declaration", R"(
function __main__():
block0:
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_f64 1.5
    store __ifexpr0, %1
    jump block3
block2:
    %2 = const_f64 2.5
    store __ifexpr0, %2
    jump block3
block3:
    %3 = load __ifexpr0
    store d, %3 : float[64]
    return
)");

    // Accepting the temporary must not mean accepting anything. The two arms
    // disagree about the type, which no source-level declaration could have
    // expressed either, so it stays an error: Lithon is statically typed and
    // `1.5 if c else 0` is not a float-or-int, it is two different types.
    expect_rejects("merge temp whose arms disagree on type", R"(
function __main__():
block0:
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_f64 1.5
    store __ifexpr0, %1
    jump block3
block2:
    %2 = const_i64 0
    store __ifexpr0, %2
    jump block3
block3:
    %3 = load __ifexpr0
    call print, %3
    return
)", "disagrees across branches");

    // Same, for widths: int[8] and int[64] arms are both ints but not the same
    // int, so the merge still has to be pinned down rather than silently
    // widened.
    expect_rejects("merge temp whose arms disagree on width", R"(
function __main__():
block0:
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_i64 7
    store __ifexpr0, %1
    jump block3
block2:
    %2 = const_i64 9
    store __ifexpr0, %2 : int[8]
    jump block3
block3:
    %3 = load __ifexpr0
    call print, %3
    return
)", "disagrees across branches");

    // A merge temp is not a hole in definite assignment: only the arms may
    // store it, and the join may only load it once every arm has. Here block2
    // stores nothing, so the load is genuinely reading an undefined value.
    expect_rejects("merge temp loaded on a path that never stored it", R"(
function __main__():
block0:
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_i64 7
    store __ifexpr0, %1
    jump block3
block2:
    jump block3
block3:
    %3 = load __ifexpr0
    call print, %3
    return
)", "not definitely assigned");

    std::printf("typecheck: 4.2 tuple construction and immutability\n");

    // A literal fills the run through the ordinary IndexStore path, so it is
    // accepted. This is the one place a tuple may be written.
    expect_accepts("tuple built by a literal of matching arity", R"(
function __main__():
block0:
    store t : tuple[int[64], 4]
    %0 = const_i64 0
    %1 = const_i64 1
    IndexStore t, %0, %1
    %2 = const_i64 1
    %3 = const_i64 2
    IndexStore t, %2, %3
    %4 = const_i64 2
    %5 = const_i64 3
    IndexStore t, %4, %5
    %6 = const_i64 3
    %7 = const_i64 4
    IndexStore t, %6, %7
    %8 = const_i64 0
    %9 = Index t, %8
    call print, %9
    return
)");

    // The all zero tuple. A bare declaration writes nothing, and that has to be
    // legal, or the partially written rule below would reject it too.
    expect_accepts("bare tuple declaration reads back as zeros", R"(
function __main__():
block0:
    store z : tuple[int[64], 3]
    %0 = const_i64 2
    %1 = Index z, %0
    call print, %1
    return
)");

    // Immutability, which is the point of the whole type.
    expect_rejects("store into a completed tuple", R"(
function __main__():
block0:
    store t : tuple[int[64], 2]
    %0 = const_i64 0
    %1 = const_i64 1
    IndexStore t, %0, %1
    %2 = const_i64 1
    %3 = const_i64 2
    IndexStore t, %2, %3
    %4 = const_i64 0
    %5 = const_i64 9
    IndexStore t, %4, %5
    return
)", "a tuple is immutable");

    // The subtle one. Counting stores is not enough to tell a real literal from
    // a write after the declaration, because a store of the declaration followed
    // by one IndexStore is a valid one element initializer for a four slot
    // tuple. What separates them is that a literal writes all N elements, so a
    // partial one is an error.
    expect_rejects("partially initialised tuple", R"(
function __main__():
block0:
    store t : tuple[int[64], 4]
    %0 = const_i64 0
    %1 = const_i64 7
    IndexStore t, %0, %1
    %2 = const_i64 0
    %3 = Index t, %2
    call print, %3
    return
)", "initializer wrote 1 of 4 elements");

    // Reading seals the tuple too. Element 0 must not be initialised by a read
    // of element 0.
    //
    // The diagnostic that comes back is the immutability one rather than the
    // partial initializer one. Closing construction removes the exemption before
    // the store is considered at all. That order is worth asserting, because the
    // alternative is a write that stays legal purely because of where it sits in
    // the block.
    expect_rejects("read cannot also initialise the tuple", R"(
function __main__():
block0:
    store t : tuple[int[64], 2]
    %0 = const_i64 0
    %1 = Index t, %0
    call print, %1
    %2 = const_i64 1
    %3 = const_i64 5
    IndexStore t, %2, %3
    return
)", "a tuple is immutable");

    // Elements go through the same rules a list uses, so a float cannot
    // initialise an int slot.
    expect_rejects("float literal into an int tuple element", R"(
function __main__():
block0:
    store t : tuple[int[64], 2]
    %0 = const_i64 0
    %1 = const_f64 2.5
    IndexStore t, %0, %1
    %2 = const_i64 1
    %3 = const_i64 1
    IndexStore t, %2, %3
    return
)", "conversion does not exist");

    // 4.2 is indexed reads only. len() on a tuple is deliberately left out.
    expect_rejects("len() on a tuple", R"(
function __main__():
block0:
    store t : tuple[int[64], 4]
    %0 = Len t
    call print, %0
    return
)", "not part of 4.2");

    // Bounds still work, and a literal index is decided at compile time.
    expect_rejects("literal index past the end of a tuple", R"(
function __main__():
block0:
    store t : tuple[int[64], 4]
    %0 = const_i64 9
    %1 = Index t, %0
    call print, %1
    return
)", "is out of range for tuple[int[64], 4]");

    // A tuple declaration with no element type must not degrade into a bare
    // kind and capacity that compares equal to something valid.
    expect_rejects("tuple with no element type", R"(
function __main__():
block0:
    store t : tuple
    %0 = const_i64 0
    %1 = Index t, %0
    call print, %1
    return
)", "needs an element type");

    // ------------------------------------------------------------------
    // 4.3. Dicts. The rules are about what a table can be, not about what it
    // holds: N buckets is fixed by the type, keys are constant so a store is
    // resolvable, and the table is filled once.
    std::printf("typecheck: dicts (4.3)\n");

    expect_accepts("dict literal, read and contains", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = DictIndex d, %0
    call print, %2
    %3 = DictContains d, %0
    call print, %3
    return
)");

    // A literal key is how construction resolves a bucket. A computed key would
    // turn construction into a run time insert, which is the probe loop the
    // whole design is built to keep out of the store path.
    expect_rejects("computed dict key", R"(
function __main__(k: int[64]) -> int[64]:
block0:
    store d : dict[int[64], int[64], 4]
    %0 = load k
    %1 = const_i64 10
    DictStore d, %0, %1
    return %1
)", "a dict key must be a constant");

    // Duplicate keys are a compile time error rather than a last write wins,
    // because in Python {1: a, 1: b} is one entry and which one survives is an
    // implementation detail nobody should depend on.
    expect_rejects("duplicate dict key", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = const_i64 20
    DictStore d, %0, %2
    return
)", "duplicate dict key");

    // Immutability, the same rule a tuple has. The illegal program is refused
    // here rather than in codegen, so there is no run time check to pay for.
    //
    // The read in the middle is what makes it illegal. Construction ends at the
    // first read, because a dict is filled by its literal and a literal has no
    // reads in it, so a second store after one is a second table rather than a
    // second entry.
    expect_rejects("store into a dict after a read", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = DictIndex d, %0
    call print, %2
    %3 = const_i64 2
    %4 = const_i64 20
    DictStore d, %3, %4
    return
)", "immutable");

    // The other way construction ends is a full table. One bucket and one entry
    // is a complete dict, so the second store has nowhere to go even though
    // nothing has read it yet.
    expect_rejects("store into a full one bucket dict", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 1]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = const_i64 2
    %3 = const_i64 20
    DictStore d, %2, %3
    return
)", "2 entries but only 1 buckets");

    // Two stores in a row with no read between them is a two entry literal, not
    // an immutability violation. Refusing it would make a dict literal
    // impossible to express.
    expect_accepts("two stores in a row is construction", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = const_i64 2
    %3 = const_i64 20
    DictStore d, %2, %3
    %4 = DictIndex d, %2
    call print, %4
    return
)");

    // A bucket count is a mask, so it has to be a power of two. 3 would make
    // bucket_of a modulo and every table a different size.
    expect_rejects("dict bucket count that is not a power of two", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 3]
    %0 = const_i64 1
    %1 = const_i64 10
    DictStore d, %0, %1
    return
)", "power of two");

    // A dict annotation carries three fields and the IR parser insists on all
    // three. A two field one is not a value-only dict, it is a typo, and it is
    // refused before anything tries to lay a table out from it.
    expect_rejects("dict with no value type", R"(
function __main__():
block0:
    store d : dict[int[64], 4]
    %0 = const_i64 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "malformed dict type annotation");

    // A key type has to be one a table can hold. There is no string key: a
    // frame slot holds a number, and 4.3 is not a step toward string keys.
    expect_rejects("string keyed dict", R"(
function __main__():
block0:
    store d : dict[str[64], int[64], 4]
    %0 = const_i64 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "dict keys are int or bool only");

    // An int key needs a width, because the key region is packed at the width
    // the type names. A widthless int would leave the stride undefined, and it
    // would be read back as a different key than it was stored as.
    expect_rejects("dict int key with no width", R"(
function __main__():
block0:
    store d : dict[int, int[64], 4]
    %0 = const_i64 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "key width must be a multiple of 8");

    // A dict is a frame of buckets, so it is not passed by value and it is not
    // returned by value. Both are refused with the reason, rather than silently
    // copying a frame layout that has no copy in the language.
    expect_rejects("dict as a parameter", R"(
function peek(d: dict[int[64], int[64], 4]) -> int[64]:
block0:
    %0 = const_i64 1
    %1 = DictIndex d, %0
    return %1
)", "cannot be a dict");

    expect_rejects("dict as a return type", R"(
function make() -> dict[int[64], int[64], 4]:
block0:
    store d : dict[int[64], int[64], 4]
    %0 = load d
    return %0
)", "cannot be a dict");

    // len() folds for a list because N is static and so is a tuple's capacity.
    // A dict has N buckets, not N entries, so len() would answer the wrong
    // question. Rejected for the same reason a tuple's is.
    expect_rejects("len() on a dict", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = Len d
    call print, %0
    return
)", "no index and no length");

    // A narrow key table stores one byte per key, so a literal that does not fit
    // is refused rather than truncated into a different key. The frontend emits
    // full width int literals, so a key that does fit is accepted and narrowed
    // by canonical_key, which is what keeps int[8] tables usable at all.
    expect_accepts("narrow dict key that fits", R"(
function __main__():
block0:
    store d : dict[int[8], int[64], 4]
    %0 = const_i64 100
    %1 = const_i64 10
    DictStore d, %0, %1
    %2 = DictIndex d, %0
    call print, %2
    return
)");

    expect_rejects("narrow dict key that does not fit", R"(
function __main__():
block0:
    store d : dict[int[8], int[64], 4]
    %0 = const_i64 300
    %1 = const_i64 10
    DictStore d, %0, %1
    return
)", "does not fit int[8]");

    // A bool key is not an int key. They are different types with different
    // stored forms, so an int table does not quietly accept a bool the way
    // Python's dict does.
    expect_rejects("bool key into an int keyed dict", R"(
function __main__():
block0:
    store d : dict[int[64], int[64], 4]
    %0 = const_bool 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "must be int[64], got bool");

    // The reverse direction is not a special case either. A bool keyed table is
    // not an int keyed one that happens to hold 0 and 1, so an int operand is
    // refused rather than silently canonicalised into a bool.
    expect_rejects("int key into a bool keyed dict", R"(
function __main__():
block0:
    store d : dict[bool, int[64], 4]
    %0 = const_i64 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "must be bool, got int[64]");

    // A value type a frame slot cannot hold. Same rule as a list element.
    expect_rejects("dict of dicts", R"(
function __main__():
block0:
    store d : dict[int[64], dict[int[64], int[64], 4], 4]
    %0 = const_i64 1
    %1 = DictIndex d, %0
    call print, %1
    return
)", "dict values are int, float or bool only");

    std::printf("typecheck: pointers (4.4)\n");

    // The happy path: addressof builds the pointer, valueof reads back through
    // it, the pointee suffix on valueof names the width of the native load.
    expect_accepts("addressof/valueof round trip", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = valueof %2 : int[64]
    call print, %3
    return
)");

    // print() of the pointer itself (not the pointee) is allowed: it prints the
    // address in hex, like C's %p / Rust's {:p}.
    expect_accepts("print of a ptr prints its address", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[16]
    %1 = addressof x
    store _p, %1 : ptr[int[16]]
    %2 = load _p
    call print, %2
    return
)");

    expect_accepts("float and bool pointees", R"(
function __main__():
block0:
    %0 = const_f64 2.5
    store x, %0 : float[64]
    %1 = addressof x
    store _p, %1 : ptr[float[64]]
    %2 = load _p
    %3 = valueof %2 : float[64]
    call print, %3
    %4 = const_bool 1
    store b, %4 : bool
    %5 = addressof b
    store _b, %5 : ptr[bool]
    %6 = load _b
    %7 = valueof %6 : bool
    call print, %7
    return
)");

    // Element-scaled pointer arithmetic on a byte offset. An int[64] pointee
    // has stride 8, so +1 (which the frontend scaled to 8 bytes) and back.
    expect_accepts("pointer add and sub by a scaled literal byte offset", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = const_i64 8
    %4 = add %2, %3
    store _q, %4 : ptr[int[64]]
    %5 = load _q
    %6 = const_i64 8
    %7 = sub %5, %6
    store _r, %7 : ptr[int[64]]
    %8 = load _r
    %9 = load _p
    %10 = eq %8, %9
    call print, %10
    return
)");

    // Pointers compare equal only in the address sense: pointer-vs-pointer or
    // pointer-vs-literal (the frontend emits `_p == 0` as an int compare).
    expect_accepts("pointer equality compares addresses", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = addressof x
    store _q, %3 : ptr[int[64]]
    %4 = load _q
    %5 = eq %2, %4
    call print, %5
    %6 = const_i64 0
    %7 = eq %2, %6
    call print, %7
    return
)");

    // Re-assigning a pointer to another pointer of the same pointee.
    expect_accepts("pointer re-assignment", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = addressof x
    store _q, %2 : ptr[int[64]]
    %3 = load _q
    store _p, %3
    return
)");

    // A pointer needs the value a variable carries; a valueof annotation must
    // be exactly the pointee, a literal cannot build a pointer, and there is no
    // null pointer, so a pointer must always arrive via addressof.
    expect_rejects("valueof of a non-pointer", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = load x
    %2 = valueof %1 : int[64]
    call print, %2
    return
)", "valueof needs a pointer");

    expect_rejects("valueof suffix that is not the pointee", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = valueof %2 : int[8]
    call print, %3
    return
)", "but this instruction is annotated int[8]");

    expect_rejects("literal into a pointer is refused", R"(
function __main__():
block0:
    %0 = const_i64 0
    store _p, %0 : ptr[int[64]]
    return
)", "there is no null pointer");

    // 0.6.10. A bare pointer declaration is a type-only declaration: it names
    // the type and nothing else, so it is accepted. The name is UNASSIGNED,
    // not null -- a read before the first addressof is the ordinary
    // "not definitely assigned", exactly as for a scalar.
    expect_accepts("a bare pointer declaration is a type-only declaration", R"(
function __main__():
block0:
    store _p : ptr[int[64]]
    return
)");

    expect_rejects("a bare pointer read before its addressof is not assigned", R"(
function __main__():
block0:
    store _p : ptr[int[64]]
    %0 = load _p
    %1 = valueof %0 : int[64]
    call print, %1
    return
)", "not definitely assigned");

    // The underscore naming rule, enforced both ways on hand-written IR.
    expect_rejects("a pointer that does not start with '_'", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store p, %1 : ptr[int[64]]
    return
)", "does not start with '_'");

    expect_rejects("an '_'-named scalar", R"(
function __main__():
block0:
    %0 = const_i64 5
    store _x, %0 : int[64]
    return
)", "a name that starts with '_' must be a pointer");

    // addressof wants a definitely-assigned plain scalar cell.
    expect_rejects("addressof of an undeclared variable", R"(
function __main__():
block0:
    %0 = addressof missing
    store _p, %0 : ptr[int[64]]
    return
)", "not definitely assigned");

    expect_rejects("addressof of a container", R"(
function __main__():
block0:
    store xs : list[int[64], 4]
    %0 = addressof xs
    store _p, %0 : ptr[int[64]]
    return
)", "plain scalar variable");

    expect_rejects("addressof of a pointer", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = addressof _p
    store _q, %2 : ptr[int[64]]
    return
)", "an address with no storage of its own");

    // Pointer arithmetic is add/sub of a literal, scaled by the pointee stride.
    expect_rejects("pointer division", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = const_i64 2
    %4 = div %2, %3
    store _q, %4 : ptr[int[64]]
    call print, %4
    return
)", "pointer division does not exist");

    expect_rejects("pointer comparison by lt/gt", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = const_i64 0
    %4 = lt %2, %3
    call print, %4
    return
)", "pointer");

    expect_rejects("non-literal pointer offset", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = const_i64 3
    store i, %2 : int[64]
    %3 = load i
    %4 = load _p
    %5 = add %4, %3
    store _q, %5 : ptr[int[64]]
    return
)", "a pointer offset must be a literal");

    expect_rejects("pointer offset that is not a multiple of the stride", R"(
function __main__():
block0:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    store _p, %1 : ptr[int[64]]
    %2 = load _p
    %3 = const_i64 4
    %4 = add %2, %3
    store _q, %4 : ptr[int[64]]
    return
)", "not a multiple of the pointee stride");

    // Pointers do not cross the function header: the header records a kind and
    // a width but no pointee, so a passed or returned pointer would degrade.
    expect_rejects("pointer parameter", R"(
function __main__():
block0:
    return
function helper(_p: ptr[int[64]]):
block1:
    return
)", "cannot be a ptr");

    expect_rejects("pointer return type", R"(
function __main__():
block0:
    return
function helper() -> ptr[int[64]]:
block1:
    %0 = const_i64 5
    store x, %0 : int[64]
    %1 = addressof x
    return %1
)", "return type cannot be a ptr");

    // A pointer to a container is refused where the pointee is still known.
    expect_rejects("pointer to a container", R"(
function __main__():
block0:
    store _p : ptr[list[int[64], 4]]
    return
)", "ptr pointees are int, float or bool only");

    std::printf("typecheck: 0.6.10 uninitialized declarations\n");

    // A type-only declaration on its own emits nothing and is accepted: no
    // store, no zeroing, no machine code.
    expect_accepts("type-only scalar declaration with no assignment", R"(
function __main__():
block0:
    store i : int[8]
    return
)");

    // The frontend's lowering of `i: int[8]; i = 5`: the declaration is a
    // valueless scalar store and the first assignment carries the type.
    expect_accepts("scalar declaration then first assignment", R"(
function __main__():
block0:
    store i : int[8]
    %0 = const_i64 5
    store i, %0 : int[8]
    %1 = load i
    call print, %1
    return
)");

    // A read before that first assignment is the ordinary 0.6.10 error.
    expect_rejects("read before the first assignment", R"(
function __main__():
block0:
    store i : int[8]
    %0 = load i
    call print, %0
    return
)", "not definitely assigned");

    // The first assignment obeys the declared type's range (V1_SPEC 0.6.5).
    expect_rejects("first assignment out of the declared range", R"(
function __main__():
block0:
    %0 = const_i64 300
    store i, %0 : int[8]
    return
)", "does not fit int[8]");

    // A valueless scalar store is a declaration, not a binding: the frontend
    // consumes it and annotates the first assignment, so a bare unannotated
    // store after it is still the 0.6.1 error.
    expect_rejects("unannotated store after a type-only declaration", R"(
function __main__():
block0:
    store i : int[8]
    %0 = const_i64 5
    store i, %0
    return
)", "without a type annotation");

    // Both arms assign it: assigned at the join.
    expect_accepts("both arms assign the declared variable", R"(
function __main__():
block0:
    store i : int[8]
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_i64 5
    store i, %1 : int[8]
    jump block3
block2:
    %2 = const_i64 6
    store i, %2 : int[8]
    jump block3
block3:
    %3 = load i
    call print, %3
    return
)");

    // Only one arm assigns it: not definitely assigned at the join.
    expect_rejects("only one arm assigns the declared variable", R"(
function __main__():
block0:
    store i : int[8]
    %0 = const_i64 1
    branch %0, block1, block2
block1:
    %1 = const_i64 5
    store i, %1 : int[8]
    jump block3
block2:
    jump block3
block3:
    %3 = load i
    call print, %3
    return
)", "not definitely assigned");

    // An assignment inside a loop body does not count after the loop.
    expect_rejects("a loop-body assignment does not escape the loop", R"(
function __main__():
block0:
    store i : int[8]
    jump block1
block1:
    %0 = const_i64 0
    branch %0, block2, block3
block2:
    %1 = const_i64 5
    store i, %1 : int[8]
    jump block1
block3:
    %2 = load i
    call print, %2
    return
)", "not definitely assigned");

    if (failures) {
        std::printf("\n%d check(s) FAILED\n", failures);
        return 1;
    }
    std::printf("\nall checks passed\n");
    return 0;
}
