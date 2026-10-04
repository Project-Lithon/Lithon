// 3.1: reassociating float addition, and the bit-level difference it buys.
//
// Every other pass in this file's neighbourhood is semantics-preserving, and
// this one deliberately is not. IEEE-754 addition is not associative, so
// `((a+b)+c)` and `(a+(b+c))` are the same real number and different doubles.
// That makes the pass a TRADE, and the test's job is to make the trade visible
// rather than to assert the flag is harmless.
//
// So the load-bearing assertion in here is a difference. The fixture is chosen
// so that the two groupings are far apart, not one ULP apart:
//
//     a=1e16  b=-1e16  c=1.0  d=1.0
//     left-associated:  ((1e16 + -1e16) + 1) + 1  ==  2.0
//     reassociated:     1e16 + (((-1e16 + 1) + 1)) == 1.0
//
// 1e16 has a ULP of 2, so the 1.0 in `b+c` is rounded away entirely and the
// answer moves by a whole unit. A reader who wants to know what the flag costs
// should not have to reason about the last bit to find out.
//
// The other half of the discipline is that the DEFAULT is untouched, and that is
// asserted by running the same input with the flag off and requiring bit-exact
// agreement with the left-associated reference. A flag that quietly changed the
// default build would pass every other test in the suite.

#include "compile_function.h"
#include "ir/ir.h"
#include "ir/text_parser.h"
#include "exec_memory.h"

#include <cstdio>
#include <string>

using namespace lithon::ir;
using namespace lithon::jit;

static int failures = 0;

static void check(bool ok, const char* what) {
    if (!ok) { std::printf("FAIL: %s\n", what); ++failures; }
}

// Four float variables, so every add in the chain has a real operand and
// nothing folds at compile time. `is_kinds` marks each ValueId a double, which
// is how the pass tells a float chain from an integer one.
// The chain, and then -- because this compiler has no proven convention for
// RETURNING a double and inventing one here would test the convention rather
// than the pass -- the result is compared against both candidate answers inside
// the IR and the verdict returned as an integer. `%16` is 10 when the value
// matches the left-associated answer and 1 when it matches the reassociated one.
// `int64_t (*)()` is a return convention the rest of the suite already proves.
static const char* kChain = R"(
function f():
block0:
    %0 = const_f64 1e16
    store a, %0
    %1 = const_f64 -1e16
    store b, %1
    %2 = const_f64 1.0
    store c, %2
    %3 = const_f64 1.0
    store d, %3
    %4 = load a
    %5 = load b
    %6 = add %4, %5
    %7 = load c
    %8 = add %6, %7
    %9 = load d
    %10 = add %8, %9
    store result, %10
    %11 = const_f64 2.0
    %12 = load result
    %13 = eq %12, %11
    %14 = const_f64 1.0
    %15 = eq %12, %14
    %17 = const_i64 10
    %18 = mul %13, %17
    %19 = add %18, %15
    return %19
)";

// The three-term chain, plus a SECOND reader of its intermediate. `%6` is the
// left operand of `%8`, so without the single-use guard the rotation would
// rebuild the sum and leave `%9` reading a value that no longer exists. This is
// the fixture that makes that guard load-bearing -- removing it used to pass
// every other test here, because in a straight chain every temporary has
// exactly one use and the guard can never fire.
static const char* kSharedOperand = R"(
function f():
block0:
    %0 = const_f64 1e16
    store a, %0
    %1 = const_f64 -1e16
    store b, %1
    %2 = const_f64 1.0
    store c, %2
    %4 = load a
    %5 = load b
    %6 = add %4, %5
    %7 = load c
    %8 = add %6, %7
    %9 = add %6, %7
    %10 = add %8, %9
    %11 = const_f64 2.0
    %12 = eq %10, %11
    return %12
)";

// The same chain over integers, where reassociating would be pointless rather
// than wrong: integer addition is associative and exact.
static const char* kChainInt = R"(
function f():
block0:
    %0 = const_i64 1
    store a, %0
    %1 = const_i64 2
    store b, %1
    %2 = const_i64 4
    store c, %2
    %3 = const_i64 8
    store d, %3
    %4 = load a
    %5 = load b
    %6 = add %4, %5
    %7 = load c
    %8 = add %6, %7
    %9 = load d
    %10 = add %8, %9
    return %10
)";
// Every temporary in these fixtures is a double except where noted, so the kind
// table is a straight "mark them all" -- what is under test is the PASS, not
// the kind inference.
static std::vector<bool> doubles_upto(const Module& m, size_t n) {
    std::vector<bool> kinds(n, true);
    (void)m;
    return kinds;
}

// The real end-to-end path: compile_module with optimize on and the flag as the
// caller set it. This is deliberately NOT "optimize by hand, then compile with
// optimize off", because that route skips the step where the pass reports the
// float values it invented and compile_module declares them. Skipping it is how
// a reassociated double ends up going through the INTEGER add path -- a garbage
// answer, not a rounding difference -- which is exactly the bug this test
// caught when the phase was first written.
static int64_t run_end_to_end(const char* text, bool flag) {
    Module m = parse_ir_text(text);
    CompileOptions options;
    options.optimize = true;
    options.ssa_pipeline = false;
    options.ffast_math_equivalent = flag;
    CompiledModule compiled = compile_module(m, options);
    auto it = compiled.function_offset.find(m.functions[0].name);
    ExecutableBuffer mem(compiled.code);
    auto fn = mem.entry<int64_t (*)()>(it->second);
    return fn();
}

// The pass in isolation, for the counts. No code is executed here, so this can
// use optimize_function directly and read OptimizeStats.
struct Count {
    int reassociated = 0;
    std::vector<ValueId> created;
};

static Count count_rotations(const char* text, bool all_float, bool flag) {
    Module m = parse_ir_text(text);
    std::vector<bool> kinds = doubles_upto(m, 64);
    if (!all_float) kinds.assign(64, false);
    OptimizePasses passes;
    passes.ffast_math_equivalent = flag;
    OptimizeStats stats = optimize_function(m.functions[0], passes, &kinds);
    Count c;
    c.reassociated = stats.float_adds_reassociated;
    c.created = stats.reassoc_float_values;
    return c;
}

int main() {
    // 1. Flag OFF: the chain stays left-associated, so the value equals the
    //    interpreter's 2.0 and the fixture returns 10. This is the property the
    //    whole test gate is measured against, asserted rather than assumed.
    check(run_end_to_end(kChain, false) == 10, "flag off: value is the left-associated 2.0");
    check(count_rotations(kChain, true, false).reassociated == 0, "flag off: nothing reassociated");

    // 2. Flag ON: two rotations, and the value has MOVED to the reassociated
    //    answer, so the fixture returns 1. Asserting that it moved is the point
    //    of the phase; asserting the exact direction is what distinguishes a
    //    different rounding from garbage.
    Count c = count_rotations(kChain, true, true);
    check(c.reassociated == 2, "flag on: two rotations of a four-term chain");
    check(run_end_to_end(kChain, true) == 1, "flag on: value moved to the reassociated 1.0");

    // 3. Every value the pass invented must be DECLARED a double, or it reaches
    //    codegen as Unknown and is added as an integer. That is what produced a
    //    nonsensical 1.01e+16 here the first time this fixture was run, and it
    //    is why the pass cannot be considered finished without this list.
    check(!c.created.empty(), "rotations declared the values they created");
    for (ValueId id : c.created) check(id != kInvalidValue, "declared value is real");

    // 4. An intermediate with a second reader is left alone: rotating would
    //    delete a value something else still needs.
    // Exactly ONE rotation here, and the count is the assertion that matters
    // less than it looks: the rotation at %10 is harmless (`%8` really is
    // single-use). The two that are REFUSED are at %8 and %9, where the
    // intermediate `%6` has a second reader. Dropping the guard rotates those
    // too, deletes `%6`, and leaves `%9` reading a value that no longer exists
    // -- which is what the value check below catches, and the reason the guard
    // is not merely conservative.
    check(count_rotations(kSharedOperand, true, true).reassociated == 1,
          "shared operand: only the safe rotation fires");
    check(run_end_to_end(kSharedOperand, true) == 1, "shared operand: result 2.0 intact");

    // 5. Integer chains are never touched.
    check(count_rotations(kChainInt, false, true).reassociated == 0, "integer chain: declined");
    check(run_end_to_end(kChainInt, true) == 15, "integer chain: result unchanged 15");

    if (failures == 0) std::printf("optimize_ffast_test: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
